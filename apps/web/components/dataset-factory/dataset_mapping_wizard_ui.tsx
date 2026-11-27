/**
 * Dataset field-mapping wizard (FTF-H011-P4; FINE_TUNING_FACTORY_RELEASE_1.md Section 22 Screen F,
 * 67.2, 67.3, 72.1, 72.2, 72.5; AF-15).
 *
 * A guided flow on the CERTIFIED FTF-H011-P3 routes, for one dataset:
 *   1. file format and its options (CSV encoding / delimiter / header, JSON container) ->
 *      `POST .../mapping-probe` (headers and at most 20 raw rows);
 *   2. map the fields (message list, a JSON messages column, or prompt/completion; role map,
 *      tool keys, group and metadata slots) -- heuristics never fill a field;
 *   3. preview -> `POST .../mapping-preview`: each row raw beside canonical, rejected rows with
 *      their machine-readable code, counts, the raw role values seen;
 *   4. review and save -> `POST .../mappings` (Idempotency-Key) as the next proposed version;
 *   5. approve or reject -> `POST .../mappings/{id}/approve|reject` (Idempotency-Key): the purpose
 *      is READ-ONLY from the dataset, the ignored message keys are acknowledged one by one, a
 *      rationale and an explicit confirmation are required.
 * The version list (`GET .../mappings`) is loaded on mount, so a saved proposal survives a reload
 * and is resumed from the server (Section 72.2: the server keeps the draft; nothing is written to
 * localStorage or sessionStorage).
 *
 * Honest interaction (Law 11/17): the server decides everything. The importer is chosen by the
 * server-verified content type, never by the format picked here; XLSX is answered by the server
 * (`importer_unavailable` until FTF-H011-P6) and shown as blocked, never faked. Only a typed 2xx
 * whose body parses strictly AND binds to what was sent counts; a 5xx, a non-JSON body or an
 * unknown code is retryable and keeps every field; a lost write is retried with the SAME
 * Idempotency-Key, so the server replays the stored outcome instead of writing twice. Masked
 * values (`{"masked": true, "chars": n}`) are shown as "Hidden", never guessed. Every data value
 * is rendered as a text node with `dir="auto"`: an HTML payload in a cell renders as text.
 */

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import {
  AlertMessage,
  Button,
  Dialog,
  EmptyState,
  ErrorSummary,
  Field,
  StatusBadge,
  kindForStatus,
  networkError,
  uiError,
  useSingleFlight,
  type ErrorSummaryItem,
  type StatusState,
  type UiError,
} from "../ui";
import { dirFor, phrase, resolveLocale, type Locale } from "../ui/phrases";

// ---------------------------------------------------------------------------------------------
// Fixed vocabularies (each mirrors a backend constant; tests/tasks/test_ftf_h011_p4.py compares
// them with the Python source).
// ---------------------------------------------------------------------------------------------

/** `workers/data/csv_importer.py` CSV_ENCODINGS. */
export const CSV_ENCODINGS = [
  "utf-8",
  "utf-8-sig",
  "utf-16-le",
  "utf-16-be",
  "windows-1256",
] as const;
/** `workers/data/csv_importer.py` CSV_DELIMITERS. */
export const CSV_DELIMITERS = [",", ";", "\t", "|"] as const;
/** The canonical roles of `DatasetMappingRoleMap` (05-interface.yaml). */
export const ROLE_TARGETS = ["user", "assistant", "system", "tool"] as const;
/** `dataset_source_connection_schema.ALLOWED_PURPOSES`. */
export const PURPOSES = [
  "training",
  "validation",
  "held_out_evaluation",
  "candidate_development",
  "baseline_only",
] as const;
export const SHAPES = ["messages", "messages_json", "prompt_completion"] as const;
export const METADATA_SLOTS = [
  "language",
  "task_category",
  "consent",
  "quality_label",
  "source_record_id",
  "event_timestamp",
] as const;
export const MAPPING_STATUSES = ["proposed", "approved", "rejected"] as const;
export const VERDICT_STATES = ["completed", "failed", "blocked"] as const;
export const ROW_OUTCOMES = ["accepted", "rejected"] as const;
/** `dataset_mapping_routes._MAX_RATIONALE`, `_MAX_LIMIT`. */
export const MAX_RATIONALE = 2000;
export const LIST_LIMIT = 200;
/** `DatasetMappingSlot.index` maximum and `DatasetMappingCsvOptions.header_row` maximum. */
export const MAX_COLUMN_INDEX = 16383;
export const MAX_HEADER_ROW = 1000000;
/** Render bounds for one value (a preview row is bounded server-side; this keeps layout bounded). */
const MAX_DEPTH = 8;
const MAX_ITEMS = 50;

export type SourceFormat = "csv" | "json" | "jsonl" | "xlsx";
export type Shape = (typeof SHAPES)[number];
type MetadataSlot = (typeof METADATA_SLOTS)[number];

type Msg = { en: string; ar: string };

// ---------------------------------------------------------------------------------------------
// Every code the P3 routes (and the RBAC dependency in front of them) can answer with has an en
// AND an ar entry. A code not in this table is shown by status with its code, never as success.
// ---------------------------------------------------------------------------------------------
export const ERROR_MESSAGES: Record<string, Msg> = {
  // RBAC dependency
  not_authenticated: { en: "You are signed out.", ar: "لقد تم تسجيل خروجك." },
  role_not_authorized: {
    en: "Your role cannot open dataset mappings in this organization.",
    ar: "لا يسمح دورك بفتح تعيينات مجموعات البيانات في هذه المؤسسة.",
  },
  tenant_mismatch: {
    en: "This dataset belongs to another organization.",
    ar: "تنتمي مجموعة البيانات هذه إلى مؤسسة أخرى.",
  },
  // routes
  not_found: {
    en: "The dataset or mapping was not found in this project.",
    ar: "لم يتم العثور على مجموعة البيانات أو التعيين في هذا المشروع.",
  },
  invalid_body: {
    en: "The request was not accepted. Check the fields and try again.",
    ar: "لم يُقبل الطلب. تحقق من الحقول وحاول مرة أخرى.",
  },
  invalid_idempotency_key: {
    en: "The request key was not accepted.",
    ar: "لم يُقبل مفتاح الطلب.",
  },
  request_too_large: { en: "The request is too large.", ar: "الطلب كبير جدًا." },
  invalid_limit: { en: "The page size was not accepted.", ar: "لم يُقبل حجم الصفحة." },
  invalid_after: { en: "The page position was not accepted.", ar: "لم يُقبل موضع الصفحة." },
  storage_not_configured: {
    en: "Dataset storage is not configured on the server.",
    ar: "لم يتم إعداد تخزين مجموعات البيانات على الخادم.",
  },
  storage_unavailable: {
    en: "Dataset storage cannot be reached right now.",
    ar: "لا يمكن الوصول إلى تخزين مجموعات البيانات الآن.",
  },
  idempotency_key_reused: {
    en: "This request key was already used for a different request.",
    ar: "تم استخدام مفتاح الطلب هذا لطلب مختلف.",
  },
  // store
  invalid_request: { en: "The request was not accepted.", ar: "لم يُقبل الطلب." },
  actor_not_permitted: {
    en: "Your account cannot change dataset mappings.",
    ar: "لا يمكن لحسابك تغيير تعيينات مجموعات البيانات.",
  },
  actor_not_auditable: {
    en: "Your account cannot be recorded in the audit log, so nothing was done.",
    ar: "لا يمكن تسجيل حسابك في سجل التدقيق، لذلك لم يتم تنفيذ أي شيء.",
  },
  approval_not_permitted: {
    en: "Only a workspace or organization administrator can approve or reject a mapping.",
    ar: "يمكن لمسؤول مساحة العمل أو المؤسسة فقط اعتماد التعيين أو رفضه.",
  },
  dataset_not_found: {
    en: "The dataset was not found in this project.",
    ar: "لم يتم العثور على مجموعة البيانات في هذا المشروع.",
  },
  purpose_not_authorized: {
    en: "The data source is not authorized for this dataset's purpose.",
    ar: "مصدر البيانات غير مصرح به لغرض مجموعة البيانات هذه.",
  },
  purpose_mismatch: {
    en: "The purpose does not match the dataset's purpose.",
    ar: "الغرض لا يطابق غرض مجموعة البيانات.",
  },
  mapping_invalid: {
    en: "The mapping is not valid for this dataset.",
    ar: "التعيين غير صالح لمجموعة البيانات هذه.",
  },
  mapping_unsupported: {
    en: "This mapping shape is not supported yet.",
    ar: "شكل التعيين هذا غير مدعوم بعد.",
  },
  critical_role_unmapped: {
    en: "The role map needs a raw role mapped to assistant.",
    ar: "يجب أن تحتوي خريطة الأدوار على دور أصلي معيّن إلى المساعد.",
  },
  tool_id_unmapped: {
    en: "Tool calls need the tool call ID key mapped.",
    ar: "تتطلب استدعاءات الأدوات تعيين مفتاح معرّف استدعاء الأداة.",
  },
  importer_unavailable: {
    en: "This file type cannot be mapped yet.",
    ar: "لا يمكن تعيين نوع الملف هذا بعد.",
  },
  source_unavailable: {
    en: "Dataset storage cannot be reached right now.",
    ar: "لا يمكن الوصول إلى تخزين مجموعات البيانات الآن.",
  },
  source_changed: {
    en: "The stored file changed while it was being read.",
    ar: "تغيّر الملف المخزن أثناء قراءته.",
  },
  preview_incomplete: {
    en: "The preview did not finish.",
    ar: "لم تكتمل المعاينة.",
  },
  mapping_not_found: {
    en: "The mapping was not found.",
    ar: "لم يتم العثور على التعيين.",
  },
  mapping_not_proposed: {
    en: "This mapping version was already decided.",
    ar: "تم البت في إصدار التعيين هذا بالفعل.",
  },
  mapping_superseded: {
    en: "A newer mapping version exists.",
    ar: "يوجد إصدار أحدث من التعيين.",
  },
  mapping_conflict: {
    en: "Another change to this mapping happened at the same time.",
    ar: "حدث تغيير آخر على هذا التعيين في الوقت نفسه.",
  },
  preview_changed: {
    en: "The preview changed since you reviewed it.",
    ar: "تغيّرت المعاينة منذ أن راجعتها.",
  },
  critical_rows_unmapped: {
    en: "Some rows use a role or message key the mapping does not cover.",
    ar: "تستخدم بعض الصفوف دورًا أو مفتاح رسالة لا يغطيه التعيين.",
  },
  ignored_keys_not_acknowledged: {
    en: "The ignored message keys were not all acknowledged.",
    ar: "لم يتم الإقرار بكل مفاتيح الرسائل المتجاهلة.",
  },
  approval_refused: {
    en: "The approval was refused.",
    ar: "تم رفض الاعتماد.",
  },
  mapping_reapproval_required: {
    en: "The source changed, so this mapping needs a new approval.",
    ar: "تغيّر المصدر، لذلك يحتاج هذا التعيين إلى اعتماد جديد.",
  },
  mapping_tampered: {
    en: "The stored mapping does not match its digest.",
    ar: "التعيين المخزن لا يطابق بصمته.",
  },
};

/** Codes that stop the flow until something outside the form changes (Section 72.2 "Blocked"). */
const BLOCKING_CODES: ReadonlySet<string> = new Set([
  "storage_unavailable",
  "storage_not_configured",
  "source_unavailable",
  "source_changed",
  "importer_unavailable",
  "purpose_not_authorized",
  "mapping_tampered",
]);
/** Codes that mean the mapping or its preview must be fixed first ("Validation required"). */
const VALIDATION_CODES: ReadonlySet<string> = new Set([
  "mapping_invalid",
  "mapping_unsupported",
  "critical_role_unmapped",
  "tool_id_unmapped",
  "critical_rows_unmapped",
  "ignored_keys_not_acknowledged",
  "preview_changed",
  "preview_incomplete",
  "mapping_reapproval_required",
]);

/** Row rejection codes with a sentence; any other code is shown as itself after "Rejected". */
export const ROW_CODE_MESSAGES: Record<string, Msg> = {
  unmapped_role: {
    en: "A message uses a raw role the role map does not cover.",
    ar: "تستخدم رسالة دورًا أصليًا لا تغطيه خريطة الأدوار.",
  },
  unmapped_message_key: {
    en: "A message has a key the mapping does not cover.",
    ar: "تحتوي رسالة على مفتاح لا يغطيه التعيين.",
  },
  missing_field: { en: "A mapped field is missing.", ar: "حقل معيّن مفقود." },
  unknown_tool: {
    en: "A tool call names a tool this project does not define.",
    ar: "يستدعي استدعاء أداة أداةً غير معرّفة في هذا المشروع.",
  },
  orphan_tool_result: {
    en: "A tool result answers no tool call.",
    ar: "نتيجة أداة لا تجيب أي استدعاء أداة.",
  },
  formula_cell: {
    en: "A cell holds a formula; only values are imported.",
    ar: "تحتوي خلية على صيغة؛ يتم استيراد القيم فقط.",
  },
};

// ---------------------------------------------------------------------------------------------
// UI words (standard system phrases only; no brand or marketing copy).
// ---------------------------------------------------------------------------------------------
const M = {
  title: { en: "Dataset field mapping", ar: "تعيين حقول مجموعة البيانات" },
  datasetLabel: { en: "Dataset", ar: "مجموعة البيانات" },
  stepsLabel: { en: "Mapping steps", ar: "خطوات التعيين" },
  stepOf: { en: "Step {n} of 5", ar: "الخطوة {n} من 5" },
  stepFormat: { en: "File format", ar: "تنسيق الملف" },
  stepMap: { en: "Map fields", ar: "تعيين الحقول" },
  stepPreview: { en: "Preview", ar: "المعاينة" },
  stepPropose: { en: "Review and save", ar: "المراجعة والحفظ" },
  stepApprove: { en: "Approve", ar: "الاعتماد" },
  stepDone: { en: "done", ar: "مكتملة" },
  stepCurrent: { en: "current", ar: "الحالية" },
  stepLocked: { en: "not available yet", ar: "غير متاحة بعد" },
  loading: { en: "Loading…", ar: "جارٍ التحميل…" },
  versionsHeading: { en: "Mapping versions", ar: "إصدارات التعيين" },
  versionsCaption: { en: "Saved mapping versions", ar: "إصدارات التعيين المحفوظة" },
  versionsFailed: {
    en: "The mapping versions could not be loaded.",
    ar: "تعذر تحميل إصدارات التعيين.",
  },
  versionsMore: {
    en: "Only the first {n} versions are listed.",
    ar: "يتم عرض أول {n} إصدار فقط.",
  },
  noVersionsTitle: { en: "No mapping yet", ar: "لا يوجد تعيين بعد" },
  noVersionsWhy: {
    en: "Map this dataset's fields before it can be imported. Start with the file format.",
    ar: "عيّن حقول مجموعة البيانات هذه قبل استيرادها. ابدأ بتنسيق الملف.",
  },
  startMapping: { en: "Choose the file format", ar: "اختر تنسيق الملف" },
  colVersion: { en: "Version", ar: "الإصدار" },
  colStatus: { en: "Status", ar: "الحالة" },
  colPurpose: { en: "Purpose", ar: "الغرض" },
  colCreated: { en: "Saved at", ar: "تاريخ الحفظ" },
  colAction: { en: "Action", ar: "الإجراء" },
  reviewVersion: { en: "Review version {n}", ar: "مراجعة الإصدار {n}" },
  formatLegend: { en: "File format", ar: "تنسيق الملف" },
  formatHelp: {
    en: "The server reads the file by its verified type; these options only say how to read it.",
    ar: "يقرأ الخادم الملف حسب نوعه المتحقق منه؛ هذه الخيارات تحدد طريقة القراءة فقط.",
  },
  fmtCsv: { en: "CSV (comma-separated text)", ar: "CSV (نص مفصول بفواصل)" },
  fmtJson: { en: "JSON (one document)", ar: "JSON (مستند واحد)" },
  fmtJsonl: { en: "JSON Lines (one record per line)", ar: "JSON Lines (سجل في كل سطر)" },
  fmtXlsx: { en: "Excel workbook (XLSX)", ar: "مصنف Excel ‏(XLSX)" },
  encodingLabel: { en: "Text encoding", ar: "ترميز النص" },
  delimiterLabel: { en: "Delimiter", ar: "الفاصل" },
  delimComma: { en: "Comma (,)", ar: "فاصلة (,)" },
  delimSemicolon: { en: "Semicolon (;)", ar: "فاصلة منقوطة (;)" },
  delimTab: { en: "Tab", ar: "مسافة جدولة" },
  delimPipe: { en: "Vertical bar (|)", ar: "شريط عمودي (|)" },
  hasHeaderLabel: { en: "The file has a header row", ar: "يحتوي الملف على صف عناوين" },
  headerRowLabel: { en: "Rows before the header", ar: "عدد الصفوف قبل العناوين" },
  headerRowHelp: {
    en: "Non-blank rows to skip before the header. 0 means the header is the first row.",
    ar: "عدد الصفوف غير الفارغة التي يتم تخطيها قبل العناوين. 0 يعني أن العناوين في الصف الأول.",
  },
  headerRowInvalid: {
    en: "Enter a whole number from 0 to 1000000.",
    ar: "أدخل عددًا صحيحًا من 0 إلى 1000000.",
  },
  headerRowNoHeader: {
    en: "Without a header row, rows before the header must be 0.",
    ar: "بدون صف عناوين، يجب أن يكون عدد الصفوف قبل العناوين 0.",
  },
  containerLabel: { en: "Records location (JSON pointer)", ar: "موضع السجلات (مؤشر JSON)" },
  containerHelp: {
    en: "Leave empty when the document is a list of records; for example /data/items otherwise.",
    ar: "اتركه فارغًا إذا كان المستند قائمة سجلات؛ وإلا مثلًا ‎/data/items.",
  },
  pointerInvalid: {
    en: "Enter a JSON pointer such as /data/items, or leave it empty.",
    ar: "أدخل مؤشر JSON مثل ‎/data/items، أو اتركه فارغًا.",
  },
  ignoredLabel: { en: "Top-level keys to ignore", ar: "المفاتيح العليا المراد تجاهلها" },
  ignoredHelp: {
    en: "One key per line. Keys beside the records that are not data.",
    ar: "مفتاح واحد في كل سطر. مفاتيح بجانب السجلات ليست بيانات.",
  },
  xlsxNotice: {
    en: "The server decides whether this workbook can be read; if not, the reason is shown.",
    ar: "يقرر الخادم ما إذا كان يمكن قراءة هذا المصنف؛ وإلا يتم عرض السبب.",
  },
  needFormat: { en: "Choose the file format.", ar: "اختر تنسيق الملف." },
  readFile: { en: "Read the file", ar: "قراءة الملف" },
  reading: { en: "Reading the file…", ar: "جارٍ قراءة الملف…" },
  probeHeading: { en: "File sample", ar: "عينة من الملف" },
  probeCaption: {
    en: "First rows of the file, as stored",
    ar: "الصفوف الأولى من الملف كما هي مخزنة",
  },
  probeFailed: {
    en: "The file could not be read with these options.",
    ar: "تعذرت قراءة الملف بهذه الخيارات.",
  },
  probeEmpty: { en: "The file has no rows to show.", ar: "لا يحتوي الملف على صفوف لعرضها." },
  maskedNotice: {
    en: "Values are hidden for your role on this data source.",
    ar: "القيم مخفية لدورك على مصدر البيانات هذا.",
  },
  hidden: { en: "Hidden", ar: "مخفي" },
  hiddenChars: { en: "Hidden ({n} characters)", ar: "مخفي ({n} حرفًا)" },
  emptyValue: { en: "(empty)", ar: "(فارغ)" },
  nullValue: { en: "(no value)", ar: "(لا توجد قيمة)" },
  moreItems: { en: "… {n} more", ar: "… {n} أخرى" },
  tooDeep: { en: "(nested too deeply to show)", ar: "(متداخل بعمق لا يسمح بعرضه)" },
  column: { en: "Column {n}", ar: "العمود {n}" },
  rowLabel: { en: "Row", ar: "الصف" },
  continue: { en: "Continue", ar: "متابعة" },
  back: { en: "Back", ar: "رجوع" },
  shapeLegend: { en: "How conversations are stored", ar: "طريقة تخزين المحادثات" },
  shapeMessages: {
    en: "A list of messages in each record",
    ar: "قائمة رسائل في كل سجل",
  },
  shapeMessagesJson: {
    en: "A column that holds the messages as JSON text",
    ar: "عمود يحتوي الرسائل كنص JSON",
  },
  shapePromptCompletion: { en: "A prompt and a completion", ar: "مطالبة وإكمال" },
  needShape: { en: "Choose how conversations are stored.", ar: "اختر طريقة تخزين المحادثات." },
  messagesPointerLabel: { en: "Messages location (JSON pointer)", ar: "موضع الرسائل (مؤشر JSON)" },
  messagesPointerHelp: {
    en: "Where the message list is in each record, for example /conversation.",
    ar: "موضع قائمة الرسائل في كل سجل، مثلًا ‎/conversation.",
  },
  messagesColumnLabel: { en: "Messages column", ar: "عمود الرسائل" },
  roleKeyLabel: { en: "Role key", ar: "مفتاح الدور" },
  contentKeyLabel: { en: "Content key", ar: "مفتاح المحتوى" },
  roleMapLegend: { en: "Role map", ar: "خريطة الأدوار" },
  roleMapHelp: {
    en: "Map each raw role value in the data to a conversation role. Nothing is filled in for you.",
    ar: "عيّن كل قيمة دور أصلية في البيانات إلى دور في المحادثة. لا يتم ملء أي شيء تلقائيًا.",
  },
  rawRoleLabel: { en: "Raw role value {n}", ar: "قيمة الدور الأصلية {n}" },
  roleTargetLabel: { en: "Maps to (role {n})", ar: "يُعيَّن إلى (الدور {n})" },
  addRole: { en: "Add a raw role", ar: "إضافة دور أصلي" },
  removeRole: { en: "Remove raw role {n}", ar: "إزالة الدور الأصلي {n}" },
  roleUser: { en: "User", ar: "المستخدم" },
  roleAssistant: { en: "Assistant", ar: "المساعد" },
  roleSystem: { en: "System", ar: "النظام" },
  roleTool: { en: "Tool", ar: "الأداة" },
  noAssistantWarning: {
    en: "No raw role maps to Assistant. The server will refuse this mapping.",
    ar: "لا يوجد دور أصلي معيّن إلى المساعد. سيرفض الخادم هذا التعيين.",
  },
  needRawRole: { en: "Enter raw role value {n}.", ar: "أدخل قيمة الدور الأصلية {n}." },
  duplicateRawRole: {
    en: "Raw role value {n} is listed twice.",
    ar: "قيمة الدور الأصلية {n} مكررة.",
  },
  needOneRole: { en: "Add at least one raw role.", ar: "أضف دورًا أصليًا واحدًا على الأقل." },
  unmappedLegend: { en: "Other keys in a message", ar: "المفاتيح الأخرى في الرسالة" },
  unmappedReject: { en: "Reject the row", ar: "رفض الصف" },
  unmappedIgnore: {
    en: "Ignore them (you confirm each one before approval)",
    ar: "تجاهلها (تؤكد كل واحد منها قبل الاعتماد)",
  },
  toolCallsKeyLabel: { en: "Tool calls key", ar: "مفتاح استدعاءات الأدوات" },
  toolCallIdKeyLabel: { en: "Tool call ID key", ar: "مفتاح معرّف استدعاء الأداة" },
  nameKeyLabel: { en: "Tool name key", ar: "مفتاح اسم الأداة" },
  promptLabel: { en: "Prompt", ar: "المطالبة" },
  completionLabel: { en: "Completion", ar: "الإكمال" },
  systemLabel: { en: "System context", ar: "سياق النظام" },
  groupKeyLabel: { en: "Conversation group ID", ar: "معرّف مجموعة المحادثة" },
  metadataLegend: { en: "Labels and source fields", ar: "التسميات وحقول المصدر" },
  metaLanguage: { en: "Language", ar: "اللغة" },
  metaTaskCategory: { en: "Task category", ar: "فئة المهمة" },
  metaConsent: { en: "Consent", ar: "الموافقة" },
  metaQuality: { en: "Quality label", ar: "تسمية الجودة" },
  metaSourceId: { en: "Source record ID", ar: "معرّف السجل المصدر" },
  metaTimestamp: { en: "Event time", ar: "وقت الحدث" },
  notMappedOption: { en: "Not mapped", ar: "غير معيّن" },
  keyHelp: {
    en: "The key name in each record, exactly as stored.",
    ar: "اسم المفتاح في كل سجل كما هو مخزن تمامًا.",
  },
  keysSeen: { en: "Keys seen in the sample:", ar: "المفاتيح في العينة:" },
  needField: { en: "Enter {field}.", ar: "أدخل {field}." },
  chooseField: { en: "Choose {field}.", ar: "اختر {field}." },
  previewButton: { en: "Preview the mapping", ar: "معاينة التعيين" },
  previewing: { en: "Building the preview…", ar: "جارٍ إنشاء المعاينة…" },
  previewHeading: { en: "Preview", ar: "المعاينة" },
  previewStale: {
    en: "The mapping changed after this preview. Preview it again.",
    ar: "تغيّر التعيين بعد هذه المعاينة. عاينه مرة أخرى.",
  },
  previewPartial: {
    en: "The preview stopped before the end of the sample; only part of it is shown.",
    ar: "توقفت المعاينة قبل نهاية العينة؛ يتم عرض جزء منها فقط.",
  },
  previewHasRejections: {
    en: "{n} rows are rejected. Fix the mapping or check the source before approval.",
    ar: "تم رفض {n} صفوف. أصلح التعيين أو تحقق من المصدر قبل الاعتماد.",
  },
  previewFailedState: { en: "The preview failed.", ar: "فشلت المعاينة." },
  countsHeading: { en: "Counts", ar: "الأعداد" },
  countSeen: { en: "Rows read", ar: "الصفوف المقروءة" },
  countAccepted: { en: "Accepted", ar: "مقبولة" },
  countRejected: { en: "Rejected", ar: "مرفوضة" },
  countOther: { en: "Rejected: {code}", ar: "مرفوضة: {code}" },
  rolesSeen: { en: "Raw role values seen", ar: "قيم الأدوار الأصلية الظاهرة" },
  roleNotInMap: { en: "not in the role map", ar: "غير موجودة في خريطة الأدوار" },
  rowHeading: { en: "Row {locator}", ar: "الصف {locator}" },
  accepted: { en: "Accepted", ar: "مقبول" },
  rejected: { en: "Rejected", ar: "مرفوض" },
  codeLabel: { en: "code", ar: "الرمز" },
  slotLabel: { en: "field", ar: "الحقل" },
  rawHeading: { en: "Raw", ar: "الأصلي" },
  canonicalHeading: { en: "Canonical", ar: "الموحّد" },
  noCanonical: {
    en: "No canonical record (the row was rejected).",
    ar: "لا يوجد سجل موحّد (تم رفض الصف).",
  },
  notMapped: { en: "Not mapped: ", ar: "غير معيّن: " },
  comparisonCaption: { en: "Type comparison", ar: "مقارنة الأنواع" },
  colSlot: { en: "Field", ar: "الحقل" },
  colRawType: { en: "Raw type", ar: "النوع الأصلي" },
  colCanonicalType: { en: "Canonical type", ar: "النوع الموحّد" },
  colIdentical: { en: "Unchanged", ar: "دون تغيير" },
  yes: { en: "Yes", ar: "نعم" },
  no: { en: "No", ar: "لا" },
  noPreviewRows: { en: "The preview has no rows.", ar: "لا تحتوي المعاينة على صفوف." },
  summaryHeading: { en: "Mapping to save", ar: "التعيين المراد حفظه" },
  summaryShape: { en: "Conversation shape", ar: "شكل المحادثة" },
  summaryFormat: { en: "File format", ar: "تنسيق الملف" },
  summaryFields: { en: "Mapped fields", ar: "الحقول المعيّنة" },
  saveMapping: { en: "Save mapping version", ar: "حفظ إصدار التعيين" },
  saving: { en: "Saving…", ar: "جارٍ الحفظ…" },
  needPreviewFirst: {
    en: "Preview the current mapping first.",
    ar: "عاين التعيين الحالي أولًا.",
  },
  savedNotice: {
    en: "Mapping version {n} saved for review.",
    ar: "تم حفظ إصدار التعيين {n} للمراجعة.",
  },
  existingNotice: {
    en: "This mapping is already saved as version {n}.",
    ar: "هذا التعيين محفوظ بالفعل كإصدار {n}.",
  },
  approveHeading: { en: "Approve mapping version {n}", ar: "اعتماد إصدار التعيين {n}" },
  purposeLabel: { en: "Dataset purpose", ar: "غرض مجموعة البيانات" },
  purposeHelp: {
    en: "Set by the dataset. It cannot be changed here.",
    ar: "يحدده نوع مجموعة البيانات. لا يمكن تغييره هنا.",
  },
  allowedPurposes: { en: "Source authorized for", ar: "المصدر مصرح به لـ" },
  previewAgain: { en: "Preview this version", ar: "معاينة هذا الإصدار" },
  previewMismatch: {
    en: "This version's stored preview differs from the one shown. Preview this version, then decide.",
    ar: "المعاينة المخزنة لهذا الإصدار تختلف عن المعروضة. عاين هذا الإصدار ثم قرر.",
  },
  ignoredKeysLegend: {
    en: "Message keys that will be ignored",
    ar: "مفاتيح الرسائل التي سيتم تجاهلها",
  },
  ignoredKeysHelp: {
    en: "Confirm each key. Its values will not be part of the dataset.",
    ar: "أكّد كل مفتاح. لن تكون قيمه جزءًا من مجموعة البيانات.",
  },
  ackKey: { en: "Ignore the key", ar: "تجاهل المفتاح" },
  rationaleLabel: { en: "Reason for the decision", ar: "سبب القرار" },
  rationaleHelp: {
    en: "Up to 2000 characters. Stored with the decision.",
    ar: "حتى 2000 حرف. يُخزَّن مع القرار.",
  },
  rationaleCount: { en: "{n} of 2000 characters", ar: "{n} من 2000 حرف" },
  needRationale: { en: "Enter the reason for the decision.", ar: "أدخل سبب القرار." },
  rationaleTooLong: {
    en: "The reason is longer than 2000 characters.",
    ar: "السبب أطول من 2000 حرف.",
  },
  needAck: { en: "Confirm every ignored key.", ar: "أكّد كل مفتاح متجاهل." },
  confirmLabel: {
    en: "I reviewed the preview rows and approve this mapping for {purpose}.",
    ar: "راجعت صفوف المعاينة وأعتمد هذا التعيين لغرض {purpose}.",
  },
  needConfirm: { en: "Confirm that you reviewed the preview.", ar: "أكّد أنك راجعت المعاينة." },
  approveButton: { en: "Approve for {purpose}", ar: "اعتماد لغرض {purpose}" },
  approving: { en: "Approving…", ar: "جارٍ الاعتماد…" },
  rejectButton: { en: "Reject this version", ar: "رفض هذا الإصدار" },
  rejectTitle: { en: "Reject mapping version {n}?", ar: "رفض إصدار التعيين {n}؟" },
  rejectBody: {
    en: "The version is kept as rejected and cannot be approved later. Save a new version to try again.",
    ar: "يُحفظ الإصدار كمرفوض ولا يمكن اعتماده لاحقًا. احفظ إصدارًا جديدًا للمحاولة مرة أخرى.",
  },
  confirmReject: { en: "Reject version", ar: "رفض الإصدار" },
  cancel: { en: "Cancel", ar: "إلغاء" },
  rejecting: { en: "Rejecting…", ar: "جارٍ الرفض…" },
  approvedNotice: {
    en: "Mapping version {n} is approved for {purpose}.",
    ar: "تم اعتماد إصدار التعيين {n} لغرض {purpose}.",
  },
  rejectedNotice: { en: "Mapping version {n} was rejected.", ar: "تم رفض إصدار التعيين {n}." },
  decidedBy: { en: "Decided by", ar: "صاحب القرار" },
  decidedAt: { en: "Approved at", ar: "تاريخ الاعتماد" },
  mappingDigest: { en: "Mapping digest", ar: "بصمة التعيين" },
  newVersion: { en: "Map a new version", ar: "تعيين إصدار جديد" },
  hiddenDocument: {
    en: "The mapping fields are hidden for your role on this data source.",
    ar: "حقول التعيين مخفية لدورك على مصدر البيانات هذا.",
  },
  malformed: {
    en: "The server's answer could not be read. Nothing was changed.",
    ar: "تعذرت قراءة رد الخادم. لم يتم تغيير أي شيء.",
  },
  malformedWrite: {
    en: "The server answered, but the answer could not be read. Check the versions list before trying again.",
    ar: "رد الخادم، لكن تعذرت قراءة الرد. تحقق من قائمة الإصدارات قبل المحاولة مرة أخرى.",
  },
  mismatch: {
    en: "The server's answer does not match this request. Nothing is shown as saved.",
    ar: "رد الخادم لا يطابق هذا الطلب. لا يتم عرض أي شيء كمحفوظ.",
  },
  requestFailed: { en: "The request failed (HTTP {status}).", ar: "فشل الطلب (HTTP {status})." },
  blockedGeneric: { en: "the server refused this step", ar: "رفض الخادم هذه الخطوة" },
  validationWhy: { en: "Validation required", ar: "يتطلب التحقق" },
  stateLabel: { en: "Mapping state", ar: "حالة التعيين" },
  purpose_training: { en: "training", ar: "التدريب" },
  purpose_validation: { en: "validation", ar: "التحقق" },
  purpose_held_out_evaluation: { en: "held-out evaluation", ar: "التقييم المحجوز" },
  purpose_candidate_development: { en: "candidate development", ar: "تطوير المرشحين" },
  purpose_baseline_only: { en: "baseline only", ar: "خط الأساس فقط" },
  retry: { en: "Try again", ar: "إعادة المحاولة" },
  reloadVersions: { en: "Reload versions", ar: "إعادة تحميل الإصدارات" },
} satisfies Record<string, Msg>;
type MsgKey = keyof typeof M;

function fill(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{([a-z]+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

function tr(key: MsgKey, locale: Locale, vars?: Record<string, string | number>): string {
  return fill(locale === "ar" ? M[key].ar : M[key].en, vars);
}

export function errorText(code: string, locale: Locale): string | null {
  const m = Object.hasOwn(ERROR_MESSAGES, code) ? ERROR_MESSAGES[code] : undefined;
  return m ? (locale === "ar" ? m.ar : m.en) : null;
}

function purposeText(purpose: string, locale: Locale): string {
  const key = `purpose_${purpose}`;
  return Object.hasOwn(M, key) ? tr(key as MsgKey, locale) : purpose;
}

// ---------------------------------------------------------------------------------------------
// Strict response parsing (UI CHECKLIST a). Anything outside the contract is `null` = malformed.
// ---------------------------------------------------------------------------------------------
const HEX64 = /^[0-9a-f]{64}$/;
type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function isStr(v: unknown): v is string {
  return typeof v === "string";
}
function isInt(v: unknown, min = 0): v is number {
  return typeof v === "number" && Number.isInteger(v) && v >= min;
}
function isNullOr<T>(v: unknown, test: (x: unknown) => x is T): v is T | null {
  return v === null || test(v);
}
function isHex(v: unknown): v is string {
  return isStr(v) && HEX64.test(v);
}
function isCounts(v: unknown): v is Record<string, number> {
  return isObj(v) && Object.values(v).every((n) => isInt(n));
}
function oneOf<T extends string>(v: unknown, values: readonly T[]): v is T {
  return isStr(v) && (values as readonly string[]).includes(v);
}

export interface Probe {
  state: (typeof VERDICT_STATES)[number];
  failureCode: string | null;
  complete: boolean;
  masked: boolean;
  headers: Json[] | null;
  rows: Json[];
}

export function parseProbe(v: unknown): Probe | null {
  if (!isObj(v)) return null;
  if (!oneOf(v.state, VERDICT_STATES)) return null;
  if (!isNullOr(v.failure_code, isStr)) return null;
  if (typeof v.complete !== "boolean" || typeof v.masked !== "boolean") return null;
  if (v.headers !== null && !Array.isArray(v.headers)) return null;
  if (Array.isArray(v.headers) && !v.headers.every((h) => isStr(h) || isMaskedMarker(h)))
    return null;
  if (!Array.isArray(v.rows) || !v.rows.every((r) => isObj(r) || Array.isArray(r))) return null;
  return {
    state: v.state,
    failureCode: v.failure_code,
    complete: v.complete,
    masked: v.masked,
    headers: (v.headers as Json[] | null) ?? null,
    rows: v.rows as Json[],
  };
}

export interface Comparison {
  slot: string;
  rawType: string;
  canonicalType: string;
  identical: boolean;
}
export interface PreviewRow {
  locator: string;
  outcome: (typeof ROW_OUTCOMES)[number];
  code: string | null;
  slot: string | null;
  rawRow: Json | null;
  notMapped: Json[];
  candidate: Record<string, Json> | null;
  comparison: Comparison[];
}
export interface Preview {
  state: (typeof VERDICT_STATES)[number];
  failureCode: string | null;
  complete: boolean;
  masked: boolean;
  counts: Record<string, number>;
  sourceSchemaSha256: string | null;
  toolDefinitionsSha256: string;
  previewSha256: string;
  observedRoleValues: Json[];
  rows: PreviewRow[];
}

function parseComparison(v: unknown): Comparison | null {
  if (!isObj(v)) return null;
  if (!isStr(v.slot) || !isStr(v.raw_type) || !isStr(v.canonical_type)) return null;
  if (typeof v.identical !== "boolean") return null;
  return {
    slot: v.slot,
    rawType: v.raw_type,
    canonicalType: v.canonical_type,
    identical: v.identical,
  };
}

function parsePreviewRow(v: unknown): PreviewRow | null {
  if (!isObj(v)) return null;
  if (!isStr(v.locator) || !oneOf(v.outcome, ROW_OUTCOMES)) return null;
  if (!isNullOr(v.code, isStr) || !isNullOr(v.slot, isStr)) return null;
  if (v.raw_row !== null && !isObj(v.raw_row) && !Array.isArray(v.raw_row)) return null;
  if (!Array.isArray(v.not_mapped)) return null;
  if (v.candidate !== null && !isObj(v.candidate)) return null;
  if (!Array.isArray(v.comparison)) return null;
  const comparison = v.comparison.map(parseComparison);
  if (comparison.some((c) => c === null)) return null;
  // A rejected row has a code; an accepted row has a canonical record.
  if (v.outcome === "rejected" && v.code === null) return null;
  if (v.outcome === "accepted" && v.candidate === null) return null;
  return {
    locator: v.locator,
    outcome: v.outcome,
    code: v.code,
    slot: v.slot,
    rawRow: v.raw_row as Json | null,
    notMapped: v.not_mapped as Json[],
    candidate: v.candidate as Record<string, Json> | null,
    comparison: comparison as Comparison[],
  };
}

export function parsePreview(v: unknown): Preview | null {
  if (!isObj(v)) return null;
  if (!oneOf(v.state, VERDICT_STATES)) return null;
  if (!isNullOr(v.failure_code, isStr)) return null;
  if (typeof v.complete !== "boolean" || typeof v.masked !== "boolean") return null;
  if (!isCounts(v.counts)) return null;
  if (!isNullOr(v.source_schema_sha256, isHex)) return null;
  if (!isHex(v.tool_definitions_sha256) || !isHex(v.preview_sha256)) return null;
  if (!Array.isArray(v.observed_role_values)) return null;
  if (!Array.isArray(v.rows)) return null;
  const rows = v.rows.map(parsePreviewRow);
  if (rows.some((r) => r === null)) return null;
  return {
    state: v.state,
    failureCode: v.failure_code,
    complete: v.complete,
    masked: v.masked,
    counts: v.counts,
    sourceSchemaSha256: v.source_schema_sha256,
    toolDefinitionsSha256: v.tool_definitions_sha256,
    previewSha256: v.preview_sha256,
    observedRoleValues: v.observed_role_values as Json[],
    rows: rows as PreviewRow[],
  };
}

export interface MappingRow {
  id: string;
  datasetId: string;
  datasetName: string;
  datasetPurpose: string;
  mappingVersion: number;
  mappingSha256: string;
  previewSha256: string;
  status: (typeof MAPPING_STATUSES)[number];
  allowedPurposes: string[];
  approvedPurpose: string | null;
  approvedBy: string | null;
  approvedAt: string | null;
  createdAt: string;
  masked: boolean;
  mappingDocument: Record<string, Json> | null;
  decision: { action: "approve" | "reject"; rationale: string | null; decidedBy: string } | null;
}

export function parseMapping(v: unknown): MappingRow | null {
  if (!isObj(v)) return null;
  if (!isStr(v.id) || !isStr(v.dataset_id) || !isStr(v.dataset_name)) return null;
  if (!isStr(v.dataset_purpose) || !isInt(v.dataset_version)) return null;
  if (!isHex(v.dataset_sha256) || !isInt(v.mapping_version, 1)) return null;
  if (!isNullOr(v.parent_mapping_version, (x): x is number => isInt(x, 1))) return null;
  if (!isHex(v.mapping_sha256) || !isStr(v.importer) || !isStr(v.importer_version)) return null;
  if (!isStr(v.transform_version)) return null;
  if (!isNullOr(v.source_schema_sha256, isHex)) return null;
  if (!isHex(v.tool_definitions_sha256) || !isHex(v.preview_sha256)) return null;
  if (!isCounts(v.preview_counts)) return null;
  if (!oneOf(v.status, MAPPING_STATUSES)) return null;
  if (!Array.isArray(v.allowed_purposes_snapshot) || !v.allowed_purposes_snapshot.every(isStr))
    return null;
  if (!isNullOr(v.approved_purpose, isStr) || !isNullOr(v.approved_by, isStr)) return null;
  if (!isNullOr(v.approver_role, isStr) || !isNullOr(v.approved_at, isStr)) return null;
  if (!isStr(v.created_by) || !isStr(v.created_at) || !isStr(v.updated_at)) return null;
  if (typeof v.masked !== "boolean") return null;
  if (v.mapping_document !== null && !isObj(v.mapping_document)) return null;
  if (v.masked && v.mapping_document !== null) return null;
  let decision: MappingRow["decision"] = null;
  if (v.decision !== null) {
    const d = v.decision;
    if (!isObj(d) || !oneOf(d.action, ["approve", "reject"] as const)) return null;
    if (!isNullOr(d.rationale, isStr) || !isStr(d.decided_by)) return null;
    decision = { action: d.action, rationale: d.rationale, decidedBy: d.decided_by };
  }
  // An approved row carries every approval column and any other row none (the P2 CHECK).
  const approved = v.status === "approved";
  const columns = [v.approved_purpose, v.approved_by, v.approver_role, v.approved_at];
  if (approved ? columns.some((c) => c === null) : columns.some((c) => c !== null)) return null;
  if (approved && v.approved_purpose !== v.dataset_purpose) return null;
  return {
    id: v.id,
    datasetId: v.dataset_id,
    datasetName: v.dataset_name,
    datasetPurpose: v.dataset_purpose,
    mappingVersion: v.mapping_version,
    mappingSha256: v.mapping_sha256,
    previewSha256: v.preview_sha256,
    status: v.status,
    allowedPurposes: v.allowed_purposes_snapshot,
    approvedPurpose: v.approved_purpose,
    approvedBy: v.approved_by,
    approvedAt: v.approved_at,
    createdAt: v.created_at,
    masked: v.masked,
    mappingDocument: v.mapping_document as Record<string, Json> | null,
    decision,
  };
}

export function parseMappingPage(
  v: unknown,
): { items: MappingRow[]; nextAfter: number | null } | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  if (!isNullOr(v.next_after, (x): x is number => isInt(x, 1))) return null;
  const items = v.items.map(parseMapping);
  if (items.some((i) => i === null)) return null;
  return { items: items as MappingRow[], nextAfter: v.next_after };
}

export function isMaskedMarker(v: unknown): v is { masked: true; chars: number } {
  return isObj(v) && v.masked === true && isInt(v.chars) && Object.keys(v).length === 2;
}

// ---------------------------------------------------------------------------------------------
// The mapping document (the FTF-0502-P1 grammar, `DatasetMappingSpec`) built from the draft.
// ---------------------------------------------------------------------------------------------
export interface RoleRow {
  raw: string;
  target: (typeof ROLE_TARGETS)[number] | "";
}
export interface Draft {
  format: SourceFormat | "";
  encoding: string;
  delimiter: string;
  hasHeader: boolean;
  headerRow: string;
  container: string;
  ignoredTopLevelKeys: string;
  shape: Shape | "";
  messagesPointer: string;
  messagesSlot: string;
  roleKey: string;
  contentKey: string;
  roles: RoleRow[];
  onUnmapped: "reject" | "ignore";
  toolCallsKey: string;
  toolCallIdKey: string;
  nameKey: string;
  prompt: string;
  completion: string;
  system: string;
  groupKey: string;
  metadata: Record<MetadataSlot, string>;
}

export function emptyDraft(): Draft {
  return {
    format: "",
    encoding: "utf-8",
    delimiter: ",",
    hasHeader: true,
    headerRow: "0",
    container: "",
    ignoredTopLevelKeys: "",
    shape: "",
    messagesPointer: "",
    messagesSlot: "",
    roleKey: "",
    contentKey: "",
    roles: [{ raw: "", target: "" }],
    onUnmapped: "reject",
    toolCallsKey: "",
    toolCallIdKey: "",
    nameKey: "",
    prompt: "",
    completion: "",
    system: "",
    groupKey: "",
    metadata: {
      language: "",
      task_category: "",
      consent: "",
      quality_label: "",
      source_record_id: "",
      event_timestamp: "",
    },
  };
}

/** RFC 6901 pointer accepted by `DatasetMappingPointer` (empty = the whole document). */
const POINTER =
  /^$|^(?:\/(?!-(?:\/|$))(?!0[0-9]+(?:\/|$))(?![0-9]{10,}(?:\/|$))(?:[^/~]|~[01])+){1,16}$/;
export function validPointer(p: string): boolean {
  return p.length <= 256 && POINTER.test(p);
}

/** Tabular sources (CSV, XLSX) are addressed by column; object sources (JSON, JSONL) by key. */
export function isTabular(format: SourceFormat | ""): boolean {
  return format === "csv" || format === "xlsx";
}

/**
 * A slot of the grammar from the draft's value. For a tabular source the draft holds a column
 * index ("0", "1", ...): the column's header is used when the probe revealed it, its index
 * otherwise (a masked header is never guessed). For an object source the draft holds the key.
 */
export function slotFor(
  value: string,
  format: SourceFormat | "",
  headers: Json[] | null,
  hasHeader: boolean,
): Record<string, Json> | null {
  if (value === "") return null;
  if (!isTabular(format)) return { key: value };
  const index = Number(value);
  if (!Number.isInteger(index) || index < 0 || index > MAX_COLUMN_INDEX) return null;
  const header = headers?.[index];
  if (hasHeader && typeof header === "string" && header.length > 0 && header.length <= 256)
    return { header };
  return { index };
}

export type FieldKey =
  | "format"
  | "headerRow"
  | "container"
  | "shape"
  | "messagesPointer"
  | "messagesSlot"
  | "roleKey"
  | "contentKey"
  | "roles"
  | "prompt"
  | "completion";
export interface DraftProblem {
  field: FieldKey | `role-${number}`;
  message: string;
}

/** Client-side checks that prevent a request the grammar cannot accept (NN/g H5). The SERVER's
 * critical checks (an assistant in the role map, tool ids) are NOT pre-empted: their refusal is
 * shown as the server sent it. */
export function draftProblems(d: Draft, locale: Locale): DraftProblem[] {
  const out: DraftProblem[] = [];
  if (d.format === "") out.push({ field: "format", message: tr("needFormat", locale) });
  if (d.format === "csv") {
    const n = Number(d.headerRow);
    if (!/^[0-9]{1,7}$/.test(d.headerRow) || n > MAX_HEADER_ROW)
      out.push({ field: "headerRow", message: tr("headerRowInvalid", locale) });
    else if (!d.hasHeader && n !== 0)
      out.push({ field: "headerRow", message: tr("headerRowNoHeader", locale) });
  }
  if (d.format === "json" && !validPointer(d.container))
    out.push({ field: "container", message: tr("pointerInvalid", locale) });
  return out;
}

export function mappingProblems(d: Draft, locale: Locale): DraftProblem[] {
  const out = draftProblems(d, locale);
  const need = (field: FieldKey, value: string, label: MsgKey, choose = false) => {
    if (value.trim() === "")
      out.push({
        field,
        message: tr(choose ? "chooseField" : "needField", locale, { field: tr(label, locale) }),
      });
  };
  if (d.shape === "") {
    out.push({ field: "shape", message: tr("needShape", locale) });
    return out;
  }
  if (d.shape === "prompt_completion") {
    need("prompt", d.prompt, "promptLabel", isTabular(d.format));
    need("completion", d.completion, "completionLabel", isTabular(d.format));
    return out;
  }
  if (d.shape === "messages") {
    if (d.messagesPointer === "" || !validPointer(d.messagesPointer))
      out.push({ field: "messagesPointer", message: tr("pointerInvalid", locale) });
  } else need("messagesSlot", d.messagesSlot, "messagesColumnLabel", isTabular(d.format));
  need("roleKey", d.roleKey, "roleKeyLabel");
  need("contentKey", d.contentKey, "contentKeyLabel");
  if (d.roles.length === 0) out.push({ field: "roles", message: tr("needOneRole", locale) });
  const seen = new Set<string>();
  d.roles.forEach((r, i) => {
    if (r.raw === "")
      out.push({ field: `role-${i}`, message: tr("needRawRole", locale, { n: i + 1 }) });
    else if (seen.has(r.raw))
      out.push({ field: `role-${i}`, message: tr("duplicateRawRole", locale, { n: i + 1 }) });
    seen.add(r.raw);
    if (r.target === "")
      out.push({
        field: `role-${i}`,
        message: tr("chooseField", locale, { field: tr("roleTargetLabel", locale, { n: i + 1 }) }),
      });
  });
  return out;
}

/** The `DatasetMappingSpec` document, or null while the draft is incomplete. Optional fields are
 * only present when the user set them; nothing is defaulted on their behalf. */
export function buildDocument(d: Draft, headers: Json[] | null): Record<string, Json> | null {
  if (mappingProblems(d, "en").length > 0 || d.shape === "") return null;
  const slot = (v: string) => slotFor(v, d.format, headers, d.hasHeader);
  const doc: Record<string, Json> = { shape: d.shape };
  if (d.shape === "prompt_completion") {
    const prompt = slot(d.prompt);
    const completion = slot(d.completion);
    if (!prompt || !completion) return null;
    doc.prompt = prompt;
    doc.completion = completion;
    const system = slot(d.system);
    if (system) doc.system = system;
  } else {
    if (d.shape === "messages") doc.messages_pointer = d.messagesPointer;
    else {
      const messages = slot(d.messagesSlot);
      if (!messages) return null;
      doc.messages = messages;
    }
    doc.role_key = d.roleKey;
    doc.content_key = d.contentKey;
    const roleMap: Record<string, Json> = {};
    for (const r of d.roles) roleMap[r.raw] = r.target;
    doc.role_map = roleMap;
    doc.on_unmapped_message_keys = d.onUnmapped;
    if (d.toolCallsKey) doc.tool_calls_key = d.toolCallsKey;
    if (d.toolCallIdKey) doc.tool_call_id_key = d.toolCallIdKey;
    if (d.nameKey) doc.name_key = d.nameKey;
  }
  const group = slot(d.groupKey);
  if (group) doc.group_key = group;
  const metadata: Record<string, Json> = {};
  for (const name of METADATA_SLOTS) {
    const s = slot(d.metadata[name]);
    if (s) metadata[name] = s;
  }
  if (Object.keys(metadata).length > 0) doc.metadata = metadata;
  const block = formatBlock(d);
  if (block) Object.assign(doc, block);
  return doc;
}

/** The probe body: the format's own options block (the server never accepts a slot here). */
export function formatBlock(d: Draft): Record<string, Json> | null {
  if (d.format === "csv")
    return {
      csv: {
        encoding: d.encoding,
        delimiter: d.delimiter,
        has_header: d.hasHeader,
        header_row: Number(d.headerRow),
      },
    };
  if (d.format === "json")
    return {
      json: {
        container: d.container,
        ignored_top_level_keys: d.ignoredTopLevelKeys
          .split("\n")
          .map((k) => k.trim())
          .filter((k) => k !== ""),
      },
    };
  return null;
}

/** Stable text of a JSON value (sorted keys) used to compare what was sent with what came back. */
export function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (isObj(v))
    return `{${Object.keys(v)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonicalJson(v[k])}`)
      .join(",")}}`;
  return JSON.stringify(v);
}

/**
 * UI CHECKLIST (b): ONE binding check between a written mapping and the request that produced it.
 * The mapping must be this dataset's, its document (when the server revealed it) must equal the
 * document sent, and for a decision the status must be the decision's result.
 */
export function bindsMapping(
  row: MappingRow,
  expect: {
    datasetId: string;
    document?: Record<string, Json> | null;
    mappingId?: string;
    status?: MappingRow["status"];
    purpose?: string;
  },
): boolean {
  if (row.datasetId !== expect.datasetId) return false;
  if (expect.mappingId !== undefined && row.id !== expect.mappingId) return false;
  if (expect.status !== undefined && row.status !== expect.status) return false;
  if (expect.purpose !== undefined && row.approvedPurpose !== expect.purpose) return false;
  if (expect.document && row.mappingDocument !== null)
    return canonicalJson(row.mappingDocument) === canonicalJson(expect.document);
  return true;
}

/** RFC 6901 resolution, as `import_common.resolve_pointer` does for an object source. */
function resolvePointer(root: Json, pointer: string): Json | undefined {
  if (pointer === "") return root;
  let cur: Json | undefined = root;
  for (const raw of pointer.slice(1).split("/")) {
    const token = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (Array.isArray(cur)) {
      if (!/^(0|[1-9][0-9]*)$/.test(token)) return undefined;
      cur = cur[Number(token)];
    } else if (isObj(cur)) {
      cur = Object.hasOwn(cur, token) ? (cur as Record<string, Json>)[token] : undefined;
    } else return undefined;
    if (cur === undefined) return undefined;
  }
  return cur;
}

function rowCell(
  slot: Json | undefined,
  row: Json | null,
  headers: Json[] | null,
): Json | undefined {
  if (!isObj(slot) || row === null) return undefined;
  if (isStr(slot.key) && isObj(row))
    return Object.hasOwn(row, slot.key) ? row[slot.key] : undefined;
  if (Array.isArray(row)) {
    if (isInt(slot.index)) return row[slot.index];
    if (isStr(slot.header) && headers) {
      const i = headers.indexOf(slot.header);
      return i >= 0 ? row[i] : undefined;
    }
  }
  return undefined;
}

/**
 * The message keys an `on_unmapped_message_keys: "ignore"` mapping drops, from the preview's
 * revealed raw rows -- the same rule as `dataset_mapping_store.ignored_message_keys`, so the user
 * acknowledges exactly the set the server will check. Empty for any other policy.
 */
export function ignoredMessageKeys(
  doc: Record<string, Json> | null,
  preview: Preview | null,
  headers: Json[] | null,
): string[] {
  if (!doc || !preview || doc.on_unmapped_message_keys !== "ignore") return [];
  if (!isStr(doc.role_key) || !isStr(doc.content_key)) return [];
  const known = new Set<string>([doc.role_key, doc.content_key]);
  for (const k of ["tool_calls_key", "tool_call_id_key", "name_key"])
    if (isStr(doc[k]) && doc[k]) known.add(doc[k] as string);
  const seen = new Set<string>();
  for (const row of preview.rows) {
    let array: Json | undefined;
    if (doc.shape === "messages") {
      if (isObj(row.rawRow) && isStr(doc.messages_pointer))
        array = resolvePointer(row.rawRow, doc.messages_pointer);
    } else {
      const cell = rowCell(doc.messages, row.rawRow, headers);
      if (isStr(cell)) {
        try {
          array = JSON.parse(cell) as Json;
        } catch {
          array = undefined;
        }
      } else array = cell;
    }
    if (!Array.isArray(array)) continue;
    for (const message of array)
      if (isObj(message))
        for (const key of Object.keys(message)) if (!known.has(key)) seen.add(key);
  }
  return [...seen].sort();
}

// ---------------------------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------------------------
export function datasetPath(api: string, org: string, project: string, dataset: string): string {
  const e = encodeURIComponent;
  return `${api}/api/v1/organizations/${e(org)}/projects/${e(project)}/datasets/${e(dataset)}`;
}

interface Reply {
  status: number;
  ok: boolean;
  body: unknown;
  /** The body was not JSON. */
  unreadable: boolean;
}

async function call(url: string, init?: RequestInit): Promise<Reply> {
  const response = await fetch(url, { credentials: "include", ...init });
  let body: unknown = undefined;
  let unreadable = false;
  try {
    body = await response.json();
  } catch {
    unreadable = true;
  }
  return { status: response.status, ok: response.ok, body, unreadable };
}

function newKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return `map-${c.randomUUID()}`;
  const bytes = new Uint8Array(16);
  c.getRandomValues(bytes);
  return `map-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

/** A failed reply as a typed error plus its stable code (if the server sent one). */
export function replyError(
  reply: Reply,
  locale: Locale,
  write: boolean,
): { error: UiError; code: string | null } {
  const code = isObj(reply.body) && isStr(reply.body.detail) ? reply.body.detail : null;
  if (reply.ok) {
    // A 2xx that is not readable or does not parse: a write may have happened.
    return {
      error: uiError(
        tr(write ? "malformedWrite" : "malformed", locale),
        write ? "verify-state" : "retry",
      ),
      code: null,
    };
  }
  const known = code !== null ? errorText(code, locale) : null;
  // 5xx (and anything unexpected) is retryable and keeps every field (Law 17): never terminal.
  let kind = reply.status >= 500 ? "retry" : kindForStatus(reply.status);
  if (code !== null && VALIDATION_CODES.has(code) && reply.status < 500) kind = "input";
  const message =
    known ??
    `${tr("requestFailed", locale, { status: reply.status })}${code !== null ? ` (${tr("codeLabel", locale)}: ${code})` : ""}`;
  return { error: uiError(message, kind), code };
}

// ---------------------------------------------------------------------------------------------
// Value rendering: text nodes only, `dir="auto"` on every data value, masked shown as Hidden.
// ---------------------------------------------------------------------------------------------
function ValueView({
  value,
  locale,
  depth = 0,
}: {
  value: Json | undefined;
  locale: Locale;
  depth?: number;
}): ReactNode {
  if (isMaskedMarker(value))
    return (
      <span data-masked="true">
        {value.chars > 0 ? tr("hiddenChars", locale, { n: value.chars }) : tr("hidden", locale)}
      </span>
    );
  if (value === null || value === undefined)
    return <span data-null="true">{tr("nullValue", locale)}</span>;
  if (typeof value === "string")
    return value === "" ? (
      <span data-empty="true">{tr("emptyValue", locale)}</span>
    ) : (
      <span
        dir="auto"
        data-value="true"
        style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
      >
        {value}
      </span>
    );
  if (typeof value === "number" || typeof value === "boolean")
    return (
      <span dir="auto" data-value="true">
        <bdi>{JSON.stringify(value)}</bdi>
      </span>
    );
  if (depth >= MAX_DEPTH) return <span>{tr("tooDeep", locale)}</span>;
  if (Array.isArray(value)) {
    const shown = value.slice(0, MAX_ITEMS);
    return (
      <ol style={{ margin: 0, paddingInlineStart: "var(--space-4)" }}>
        {shown.map((item, i) => (
          <li key={i}>
            <ValueView value={item} locale={locale} depth={depth + 1} />
          </li>
        ))}
        {value.length > MAX_ITEMS ? (
          <li>{tr("moreItems", locale, { n: value.length - MAX_ITEMS })}</li>
        ) : null}
      </ol>
    );
  }
  const keys = Object.keys(value);
  return (
    <dl style={{ margin: 0, display: "grid", gap: "var(--space-1)" }}>
      {keys.slice(0, MAX_ITEMS).map((k) => (
        <div key={k} style={{ minInlineSize: 0 }}>
          <dt style={{ fontWeight: 600 }}>
            <span dir="auto" style={{ overflowWrap: "anywhere" }}>
              {k}
            </span>
          </dt>
          <dd style={{ marginInlineStart: "var(--space-3)", minInlineSize: 0 }}>
            <ValueView value={value[k]} locale={locale} depth={depth + 1} />
          </dd>
        </div>
      ))}
      {keys.length > MAX_ITEMS ? (
        <div>{tr("moreItems", locale, { n: keys.length - MAX_ITEMS })}</div>
      ) : null}
    </dl>
  );
}

function NameView({ value, locale }: { value: Json | undefined; locale: Locale }): ReactNode {
  if (isMaskedMarker(value)) return <span data-masked="true">{tr("hidden", locale)}</span>;
  if (typeof value === "string")
    return (
      <span dir="auto" style={{ overflowWrap: "anywhere" }}>
        {value}
      </span>
    );
  return <ValueView value={value} locale={locale} />;
}

// ---------------------------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------------------------
export interface DatasetMappingWizardProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  datasetId: string;
  locale?: Locale;
}

export function DatasetMappingWizard(props: DatasetMappingWizardProps) {
  // Any identity change fully remounts (UI CHECKLIST c): a reply for the old dataset can never
  // land on the new one, and the draft never carries over.
  return (
    <Inner
      key={`${props.apiBaseUrl}|${props.organizationId}|${props.projectId}|${props.datasetId}`}
      {...props}
    />
  );
}

type Step = 1 | 2 | 3 | 4 | 5;
type Loaded<T> =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; error: UiError; code: string | null };

interface Blocker {
  code: string;
  message: string;
}

function Inner({
  apiBaseUrl,
  organizationId,
  projectId,
  datasetId,
  locale = "en",
}: DatasetMappingWizardProps) {
  const lang = resolveLocale(locale);
  const dir = dirFor(locale);
  const uid = useId();
  const base = datasetPath(apiBaseUrl, organizationId, projectId, datasetId);

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const [step, setStep] = useState<Step>(1);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [versions, setVersions] = useState<
    Loaded<{ items: MappingRow[]; nextAfter: number | null }>
  >({
    state: "loading",
  });
  const [probe, setProbe] = useState<Loaded<Probe>>({ state: "idle" });
  const [preview, setPreview] = useState<Loaded<Preview>>({ state: "idle" });
  const [previewDoc, setPreviewDoc] = useState<string>("");
  const [mapping, setMapping] = useState<MappingRow | null>(null);
  const [saveError, setSaveError] = useState<UiError | null>(null);
  const [saving, setSaving] = useState(false);
  const [deciding, setDeciding] = useState<"" | "approve" | "reject">("");
  const [decisionError, setDecisionError] = useState<UiError | null>(null);
  const [rationale, setRationale] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [acked, setAcked] = useState<string[]>([]);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [problems, setProblems] = useState<ErrorSummaryItem[]>([]);
  const [submitCount, setSubmitCount] = useState(0);
  const [blocker, setBlocker] = useState<Blocker | null>(null);
  const [notice, setNotice] = useState("");
  const noticeRef = useRef<HTMLParagraphElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);
  const rejectOpener = useRef<HTMLElement | null>(null);
  const flight = useSingleFlight();
  // One Idempotency-Key per distinct write: a retry of the same write reuses it (the server then
  // replays the stored outcome); a different write gets a new one.
  const writeKeys = useRef<Map<string, string>>(new Map());
  const keyFor = (action: string) => {
    let k = writeKeys.current.get(action);
    if (!k) {
      k = newKey();
      writeKeys.current.set(action, k);
    }
    return k;
  };
  const seq = useRef(0);

  const loadVersions = useCallback(async () => {
    const mine = ++seq.current;
    const current = () => alive.current && mine === seq.current;
    setVersions({ state: "loading" });
    try {
      const r = await call(`${base}/mappings?limit=${LIST_LIMIT}`);
      if (!current()) return;
      const page = r.ok && !r.unreadable ? parseMappingPage(r.body) : null;
      if (page && page.items.every((m) => m.datasetId === datasetId)) {
        setVersions({ state: "ok", data: page });
        return;
      }
      if (r.ok) {
        setVersions({
          state: "error",
          error: uiError(tr("malformed", locale), "retry"),
          code: null,
        });
        return;
      }
      const { error, code } = replyError(r, locale, false);
      setVersions({ state: "error", error, code });
    } catch (caught) {
      if (current())
        setVersions({ state: "error", error: networkError(caught, locale), code: null });
    }
  }, [base, datasetId, locale]);

  useEffect(() => {
    void loadVersions();
  }, [loadVersions]);

  // Focus the notice after a successful step so a keyboard / screen-reader user hears it.
  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);

  const tabular = isTabular(draft.format);
  const headers = probe.state === "ok" ? probe.data.headers : null;
  const document_ = buildDocument(draft, headers);
  const documentText = document_ ? canonicalJson(document_) : "";
  const previewCurrent =
    preview.state === "ok" && previewDoc === documentText && documentText !== "";

  function update(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }));
    setNotice("");
  }

  /** A format option changed: the sample read with the old options no longer describes the file. */
  function updateFormat(patch: Partial<Draft>) {
    update(patch);
    setProbe({ state: "idle" });
    setPreview({ state: "idle" });
    setBlocker(null);
  }

  function blockFrom(code: string | null, error: UiError) {
    if (code !== null && BLOCKING_CODES.has(code)) setBlocker({ code, message: error.message });
  }

  // ------------------------------------------------------------------ step 1: probe
  async function runProbe() {
    const found = draftProblems(draft, locale).map((p) => ({
      fieldId: `${uid}-${p.field}`,
      message: p.message,
    }));
    setProblems(found);
    if (found.length > 0) {
      setSubmitCount((n) => n + 1);
      return;
    }
    if (!flight.begin()) return;
    setBlocker(null);
    const body = formatBlock(draft) ?? {};
    setProbe({ state: "loading" });
    try {
      const r = await call(`${base}/mapping-probe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!alive.current) return;
      const parsed = r.ok && !r.unreadable ? parseProbe(r.body) : null;
      if (parsed) {
        setProbe({ state: "ok", data: parsed });
        if (parsed.state === "completed") setStep(2);
        return;
      }
      const { error, code } = replyError(r, locale, false);
      setProbe({ state: "error", error, code });
      blockFrom(code, error);
    } catch (caught) {
      if (alive.current)
        setProbe({ state: "error", error: networkError(caught, locale), code: null });
    } finally {
      flight.end();
    }
  }

  // ------------------------------------------------------------------ step 3: preview
  async function runPreview(doc: Record<string, Json> | null = document_) {
    if (!doc) {
      const found = mappingProblems(draft, locale).map((p) => ({
        fieldId: `${uid}-${p.field}`,
        message: p.message,
      }));
      setProblems(found);
      setSubmitCount((n) => n + 1);
      return;
    }
    if (!flight.begin()) return;
    setProblems([]);
    setBlocker(null);
    const text = canonicalJson(doc);
    setPreview({ state: "loading" });
    try {
      const r = await call(`${base}/mapping-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mapping: doc }),
      });
      if (!alive.current) return;
      const parsed = r.ok && !r.unreadable ? parsePreview(r.body) : null;
      if (parsed) {
        setPreview({ state: "ok", data: parsed });
        setPreviewDoc(text);
        setAcked([]);
        return;
      }
      const { error, code } = replyError(r, locale, false);
      setPreview({ state: "error", error, code });
      blockFrom(code, error);
    } catch (caught) {
      if (alive.current)
        setPreview({ state: "error", error: networkError(caught, locale), code: null });
    } finally {
      flight.end();
    }
  }

  // ------------------------------------------------------------------ step 4: propose
  async function save() {
    if (!document_ || !previewCurrent) return;
    if (!flight.begin()) return;
    setSaving(true);
    setSaveError(null);
    setBlocker(null);
    const sent = document_;
    const key = keyFor(`propose|${documentText}`);
    try {
      const r = await call(`${base}/mappings`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify({ mapping: sent }),
      });
      if (!alive.current) return;
      const row = r.ok && !r.unreadable ? parseMapping(r.body) : null;
      if (row) {
        if (!bindsMapping(row, { datasetId, document: sent })) {
          setSaveError(uiError(tr("mismatch", locale), "verify-state"));
          return;
        }
        const created = isObj(r.body) && r.body.created === true;
        setMapping(row);
        setRationale("");
        setConfirmed(false);
        setStep(5);
        setNotice(
          tr(created ? "savedNotice" : "existingNotice", locale, { n: row.mappingVersion }),
        );
        void loadVersions();
        return;
      }
      const { error, code } = replyError(r, locale, r.ok);
      setSaveError(error);
      blockFrom(code, error);
    } catch (caught) {
      if (alive.current) setSaveError(networkError(caught, locale, { write: true }));
    } finally {
      if (alive.current) setSaving(false);
      flight.end();
    }
  }

  // ------------------------------------------------------------------ step 5: decide
  const decisionDoc = mapping?.mappingDocument ?? null;
  const decisionPreviewMatches =
    mapping !== null &&
    preview.state === "ok" &&
    preview.data.previewSha256 === mapping.previewSha256 &&
    (decisionDoc === null || previewDoc === canonicalJson(decisionDoc));
  const ignored = ignoredMessageKeys(
    decisionDoc,
    preview.state === "ok" ? preview.data : null,
    headers,
  );

  function decisionProblems(action: "approve" | "reject"): ErrorSummaryItem[] {
    const out: ErrorSummaryItem[] = [];
    const text = rationale;
    if (text.trim() === "")
      out.push({ fieldId: `${uid}-rationale`, message: tr("needRationale", locale) });
    else if (text.length > MAX_RATIONALE)
      out.push({ fieldId: `${uid}-rationale`, message: tr("rationaleTooLong", locale) });
    if (action === "approve") {
      if (ignored.some((k) => !acked.includes(k)))
        out.push({ fieldId: `${uid}-ack-0`, message: tr("needAck", locale) });
      if (!confirmed) out.push({ fieldId: `${uid}-confirm`, message: tr("needConfirm", locale) });
    }
    return out;
  }

  async function decide(action: "approve" | "reject") {
    if (!mapping) return;
    const found = decisionProblems(action);
    setProblems(found);
    if (found.length > 0) {
      setSubmitCount((n) => n + 1);
      return;
    }
    if (action === "approve" && (!decisionPreviewMatches || preview.state !== "ok")) return;
    if (!flight.begin()) return;
    setDeciding(action);
    setDecisionError(null);
    setBlocker(null);
    const body =
      action === "approve" && preview.state === "ok"
        ? {
            expected_preview_sha256: preview.data.previewSha256,
            purpose: mapping.datasetPurpose,
            acknowledged_ignored_keys: [...acked].filter((k) => ignored.includes(k)).sort(),
            rationale,
          }
        : { rationale };
    const key = keyFor(`${action}|${mapping.id}|${canonicalJson(body)}`);
    try {
      const r = await call(`${base}/mappings/${encodeURIComponent(mapping.id)}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(body),
      });
      if (!alive.current) return;
      const row = r.ok && !r.unreadable ? parseMapping(r.body) : null;
      if (row) {
        const status = action === "approve" ? "approved" : "rejected";
        const binds = bindsMapping(row, {
          datasetId,
          mappingId: mapping.id,
          status,
          ...(action === "approve" ? { purpose: mapping.datasetPurpose } : {}),
        });
        if (!binds) {
          setDecisionError(uiError(tr("mismatch", locale), "verify-state"));
          return;
        }
        setMapping(row);
        setRejectOpen(false);
        setNotice(
          action === "approve"
            ? tr("approvedNotice", locale, {
                n: row.mappingVersion,
                purpose: purposeText(row.datasetPurpose, locale),
              })
            : tr("rejectedNotice", locale, { n: row.mappingVersion }),
        );
        void loadVersions();
        return;
      }
      const { error, code } = replyError(r, locale, r.ok);
      setDecisionError(error);
      setRejectOpen(false);
      blockFrom(code, error);
    } catch (caught) {
      if (alive.current) {
        setDecisionError(networkError(caught, locale, { write: true }));
        setRejectOpen(false);
      }
    } finally {
      if (alive.current) setDeciding("");
      flight.end();
    }
  }

  useEffect(() => {
    if (decisionError || saveError)
      errorRef.current?.querySelector<HTMLElement>("[role=alert]")?.focus();
  }, [decisionError, saveError]);

  /** Resume a saved version from the server (Section 72.2: the server holds the draft). */
  function review(row: MappingRow) {
    setMapping(row);
    setDecisionError(null);
    setRationale("");
    setConfirmed(false);
    setAcked([]);
    setPreview({ state: "idle" });
    setPreviewDoc("");
    setNotice("");
    setStep(5);
  }

  function startNew() {
    setMapping(null);
    setDecisionError(null);
    setSaveError(null);
    setNotice("");
    setStep(1);
  }

  // ------------------------------------------------------------------ state word (Section 72.2)
  function overall(): { state: StatusState; detail?: string } {
    if (blocker) return { state: "blocked", detail: blocker.message };
    if (mapping?.status === "approved") return { state: "approved" };
    if (mapping?.status === "rejected") return { state: "rejected" };
    if (preview.state === "error" && preview.code && VALIDATION_CODES.has(preview.code))
      return { state: "validation_required" };
    if (decisionError && step === 5) return { state: "validation_required" };
    if (preview.state === "ok") {
      const p = preview.data;
      if (p.state !== "completed" || !p.complete || (p.counts.rejected ?? 0) > 0)
        return { state: "validation_required" };
      if (step >= 3 && !previewCurrent && mapping === null) return { state: "validation_required" };
    }
    return { state: "draft" };
  }
  const status = overall();

  // ------------------------------------------------------------------ rendering helpers
  const stepNames: MsgKey[] = [
    "stepFormat",
    "stepMap",
    "stepPreview",
    "stepPropose",
    "stepApprove",
  ];
  const reachable = (s: Step): boolean => {
    if (s === 1) return true;
    if (s === 2) return probe.state === "ok" && probe.data.state === "completed";
    if (s === 3) return reachable(2) && document_ !== null;
    if (s === 4) return reachable(3) && previewCurrent;
    return mapping !== null;
  };

  function fieldError(field: string): string | undefined {
    return problems.find((p) => p.fieldId === `${uid}-${field}`)?.message;
  }

  function columnOptions(): { value: string; label: ReactNode; text: string }[] {
    if (probe.state !== "ok") return [];
    const width = Math.max(
      probe.data.headers?.length ?? 0,
      ...probe.data.rows.map((r) => (Array.isArray(r) ? r.length : 0)),
    );
    const out: { value: string; label: ReactNode; text: string }[] = [];
    for (let i = 0; i < Math.min(width, MAX_COLUMN_INDEX + 1); i++) {
      const h = probe.data.headers?.[i];
      const name = typeof h === "string" && draft.hasHeader ? h : null;
      const columnWord = tr("column", locale, { n: i + 1 });
      out.push({
        value: String(i),
        label: name ? `${columnWord}: ${name}` : columnWord,
        text: name ?? columnWord,
      });
    }
    return out;
  }

  function seenKeys(): string[] {
    if (probe.state !== "ok" || probe.data.masked) return [];
    const keys = new Set<string>();
    for (const r of probe.data.rows) if (isObj(r)) for (const k of Object.keys(r)) keys.add(k);
    return [...keys].sort().slice(0, MAX_ITEMS);
  }

  function SlotInput({
    field,
    label,
    value,
    onChange,
    required,
  }: {
    field: string;
    label: string;
    value: string;
    onChange: (v: string) => void;
    required: boolean;
  }) {
    const id = `${uid}-${field}`;
    const err = fieldError(field);
    if (tabular) {
      const errorId = err ? `${id}-error` : undefined;
      return (
        <div>
          <label htmlFor={id} style={{ display: "block", marginBlockEnd: "var(--space-1)" }}>
            {label} <span>{required ? phraseRequired(locale) : phraseOptional(locale)}</span>
          </label>
          <select
            id={id}
            value={value}
            aria-required={required || undefined}
            aria-invalid={err ? true : undefined}
            aria-describedby={errorId}
            onChange={(e) => onChange(e.target.value)}
            style={{
              minBlockSize: "var(--target-min)",
              inlineSize: "100%",
              maxInlineSize: "32rem",
            }}
          >
            <option value="">{tr("notMappedOption", locale)}</option>
            {columnOptions().map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {err ? (
            <div id={errorId} role="alert" style={{ overflowWrap: "anywhere" }}>
              {err}
            </div>
          ) : null}
        </div>
      );
    }
    return (
      <Field
        id={id}
        label={label}
        requirement={required ? "required" : "optional"}
        locale={locale}
        value={value}
        error={err}
        maxLength={256}
        dir="auto"
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        style={{ maxInlineSize: "100%" }}
      />
    );
  }

  // ------------------------------------------------------------------ sections
  function versionsSection() {
    if (versions.state === "loading" || versions.state === "idle")
      return (
        <p role="status" aria-busy="true" data-testid="mapping-versions-loading">
          {tr("loading", locale)}
        </p>
      );
    if (versions.state === "error")
      return (
        <div data-testid="mapping-versions-error">
          <AlertMessage error={versions.error} locale={locale} testId="mapping-versions-alert" />
          <Button onClick={() => void loadVersions()}>{tr("reloadVersions", locale)}</Button>
        </div>
      );
    const items = versions.data.items;
    if (items.length === 0)
      return (
        <div data-testid="mapping-versions-empty">
          <EmptyState
            title={tr("noVersionsTitle", locale)}
            why={tr("noVersionsWhy", locale)}
            action={
              <Button
                variant="primary"
                onClick={() => document.getElementById(`${uid}-format-0`)?.focus()}
              >
                {tr("startMapping", locale)}
              </Button>
            }
          />
        </div>
      );
    return (
      <div
        role="region"
        aria-label={tr("versionsCaption", locale)}
        tabIndex={0}
        style={{ overflowX: "auto" }}
        data-testid="mapping-versions"
      >
        <table style={{ fontSize: "var(--font-table)" }}>
          <caption>{tr("versionsCaption", locale)}</caption>
          <thead>
            <tr>
              <th scope="col">{tr("colVersion", locale)}</th>
              <th scope="col">{tr("colStatus", locale)}</th>
              <th scope="col">{tr("colPurpose", locale)}</th>
              <th scope="col">{tr("colCreated", locale)}</th>
              <th scope="col">{tr("colAction", locale)}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((m) => (
              <tr key={m.id} data-testid={`mapping-version-${m.mappingVersion}`}>
                <td>{m.mappingVersion}</td>
                <td>
                  <StatusBadge
                    state={m.status === "proposed" ? "draft" : m.status}
                    locale={locale}
                  />
                </td>
                <td>{purposeText(m.datasetPurpose, locale)}</td>
                <td>
                  <bdi>{formatTime(m.createdAt, lang)}</bdi>
                </td>
                <td>
                  <Button
                    onClick={() => review(m)}
                    data-testid={`review-version-${m.mappingVersion}`}
                  >
                    {tr("reviewVersion", locale, { n: m.mappingVersion })}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {versions.data.nextAfter !== null ? (
          <p>{tr("versionsMore", locale, { n: items.length })}</p>
        ) : null}
      </div>
    );
  }

  function formatSection() {
    const formats: [SourceFormat, MsgKey][] = [
      ["csv", "fmtCsv"],
      ["json", "fmtJson"],
      ["jsonl", "fmtJsonl"],
      ["xlsx", "fmtXlsx"],
    ];
    const err = fieldError("format");
    const delims: [string, MsgKey][] = [
      [",", "delimComma"],
      [";", "delimSemicolon"],
      ["\t", "delimTab"],
      ["|", "delimPipe"],
    ];

    return (
      <section aria-labelledby={`${uid}-h-format`} data-testid="mapping-step-format">
        <h3 id={`${uid}-h-format`}>{tr("stepFormat", locale)}</h3>
        <fieldset
          id={`${uid}-format`}
          tabIndex={-1}
          aria-describedby={`${uid}-format-help${err ? ` ${uid}-format-error` : ""}`}
          aria-invalid={err ? true : undefined}
        >
          <legend>
            {tr("formatLegend", locale)} <span>{phraseRequired(locale)}</span>
          </legend>
          <p id={`${uid}-format-help`}>{tr("formatHelp", locale)}</p>
          {formats.map(([f, label], i) => (
            <label
              key={f}
              style={{
                display: "flex",
                gap: "var(--space-2)",
                alignItems: "center",
                minBlockSize: "var(--target-min)",
              }}
            >
              <input
                type="radio"
                id={`${uid}-format-${i}`}
                name={`${uid}-format`}
                value={f}
                checked={draft.format === f}
                onChange={() => {
                  update({ format: f });
                  setProbe({ state: "idle" });
                  setPreview({ state: "idle" });
                  setBlocker(null);
                }}
                style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
              />
              {tr(label, locale)}
            </label>
          ))}
          {err ? (
            <div id={`${uid}-format-error`} role="alert">
              {err}
            </div>
          ) : null}
        </fieldset>
        {draft.format === "csv" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: "var(--space-3)",
            }}
          >
            <div>
              <label
                htmlFor={`${uid}-encoding`}
                style={{ display: "block", marginBlockEnd: "var(--space-1)" }}
              >
                {tr("encodingLabel", locale)} <span>{phraseRequired(locale)}</span>
              </label>
              <select
                id={`${uid}-encoding`}
                value={draft.encoding}
                aria-required
                onChange={(e) => updateFormat({ encoding: e.target.value })}
                style={{ minBlockSize: "var(--target-min)" }}
              >
                {CSV_ENCODINGS.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor={`${uid}-delimiter`}
                style={{ display: "block", marginBlockEnd: "var(--space-1)" }}
              >
                {tr("delimiterLabel", locale)} <span>{phraseRequired(locale)}</span>
              </label>
              <select
                id={`${uid}-delimiter`}
                value={draft.delimiter}
                aria-required
                onChange={(e) => updateFormat({ delimiter: e.target.value })}
                style={{ minBlockSize: "var(--target-min)" }}
              >
                {delims.map(([d, label]) => (
                  <option key={label} value={d}>
                    {tr(label, locale)}
                  </option>
                ))}
              </select>
            </div>
            <label style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
              <input
                type="checkbox"
                id={`${uid}-hasHeader`}
                checked={draft.hasHeader}
                onChange={(e) =>
                  updateFormat({
                    hasHeader: e.target.checked,
                    ...(e.target.checked ? {} : { headerRow: "0" }),
                  })
                }
                style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
              />
              {tr("hasHeaderLabel", locale)}
            </label>
            <Field
              id={`${uid}-headerRow`}
              label={tr("headerRowLabel", locale)}
              helper={tr("headerRowHelp", locale)}
              requirement="required"
              locale={locale}
              inputMode="numeric"
              value={draft.headerRow}
              error={fieldError("headerRow")}
              onChange={(e) => updateFormat({ headerRow: e.target.value })}
            />
          </div>
        ) : null}
        {draft.format === "json" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: "var(--space-3)",
            }}
          >
            <Field
              id={`${uid}-container`}
              label={tr("containerLabel", locale)}
              helper={tr("containerHelp", locale)}
              requirement="optional"
              locale={locale}
              dir="ltr"
              value={draft.container}
              error={fieldError("container")}
              maxLength={256}
              spellCheck={false}
              autoComplete="off"
              onChange={(e) => updateFormat({ container: e.target.value })}
            />
            <div>
              <label
                htmlFor={`${uid}-ignored`}
                style={{ display: "block", marginBlockEnd: "var(--space-1)" }}
              >
                {tr("ignoredLabel", locale)} <span>{phraseOptional(locale)}</span>
              </label>
              <div id={`${uid}-ignored-helper`}>{tr("ignoredHelp", locale)}</div>
              <textarea
                id={`${uid}-ignored`}
                aria-describedby={`${uid}-ignored-helper`}
                value={draft.ignoredTopLevelKeys}
                dir="auto"
                rows={3}
                spellCheck={false}
                onChange={(e) => updateFormat({ ignoredTopLevelKeys: e.target.value })}
                style={{ inlineSize: "100%", maxInlineSize: "32rem", boxSizing: "border-box" }}
              />
            </div>
          </div>
        ) : null}
        {draft.format === "xlsx" ? (
          <p data-testid="xlsx-notice">{tr("xlsxNotice", locale)}</p>
        ) : null}
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginBlockStart: "var(--space-3)",
          }}
        >
          <Button
            variant="primary"
            data-testid="probe-button"
            onClick={() => void runProbe()}
            disabled={probe.state === "loading"}
          >
            {tr(probe.state === "loading" ? "reading" : "readFile", locale)}
          </Button>
          {reachable(2) ? (
            <Button onClick={() => setStep(2)} data-testid="to-map">
              {tr("continue", locale)}
            </Button>
          ) : null}
        </div>
        {probe.state === "loading" ? (
          <p role="status" aria-busy="true" data-testid="probe-loading">
            {tr("reading", locale)}
          </p>
        ) : null}
        {probe.state === "error" ? (
          <div data-testid="probe-error">
            <AlertMessage error={probe.error} locale={locale} testId="probe-alert" />
          </div>
        ) : null}
        {probe.state === "ok" && probe.data.state !== "completed" ? probeView(probe.data) : null}
      </section>
    );
  }

  function probeView(p: Probe) {
    const width = Math.max(
      p.headers?.length ?? 0,
      ...p.rows.map((r) => (Array.isArray(r) ? r.length : 0)),
    );
    const isArrayRows = p.rows.some((r) => Array.isArray(r)) || p.headers !== null;
    return (
      <div data-testid="probe-result">
        <h4>{tr("probeHeading", locale)}</h4>
        {p.masked ? <p data-testid="masked-notice">{tr("maskedNotice", locale)}</p> : null}
        {p.state !== "completed" ? (
          <AlertMessage
            error={uiError(
              `${tr("probeFailed", locale)}${p.failureCode ? ` ${errorText(p.failureCode, locale) ?? ""} (${tr("codeLabel", locale)}: ${p.failureCode})` : ""}`,
              "input",
            )}
            locale={locale}
            testId="probe-failed"
          />
        ) : null}
        {p.rows.length === 0 ? (
          <p>{tr("probeEmpty", locale)}</p>
        ) : isArrayRows ? (
          <div
            role="region"
            aria-label={tr("probeCaption", locale)}
            tabIndex={0}
            style={{ overflowX: "auto" }}
          >
            <table style={{ fontSize: "var(--font-table)" }} data-testid="probe-table">
              <caption>{tr("probeCaption", locale)}</caption>
              <thead>
                <tr>
                  <th scope="col">{tr("rowLabel", locale)}</th>
                  {Array.from({ length: width }, (_, i) => (
                    <th scope="col" key={i}>
                      {p.headers && p.headers[i] !== undefined ? (
                        <NameView value={p.headers[i]} locale={locale} />
                      ) : (
                        tr("column", locale, { n: i + 1 })
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {p.rows.map((r, ri) => (
                  <tr key={ri}>
                    <th scope="row">{ri + 1}</th>
                    {Array.from({ length: width }, (_, ci) => (
                      <td
                        key={ci}
                        style={{
                          minInlineSize: "6rem",
                          maxInlineSize: "24rem",
                          verticalAlign: "top",
                        }}
                      >
                        <ValueView value={Array.isArray(r) ? r[ci] : undefined} locale={locale} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ol
            data-testid="probe-records"
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: "var(--space-3)",
              paddingInlineStart: "var(--space-4)",
            }}
          >
            {p.rows.map((r, ri) => (
              <li key={ri} style={{ minInlineSize: 0 }}>
                <ValueView value={r} locale={locale} />
              </li>
            ))}
          </ol>
        )}
      </div>
    );
  }

  function mapSection() {
    const shapes: [Shape, MsgKey][] = [
      ["messages", "shapeMessages"],
      ["messages_json", "shapeMessagesJson"],
      ["prompt_completion", "shapePromptCompletion"],
    ];
    const available = shapes.filter(([s]) => !(s === "messages" && tabular));
    const err = fieldError("shape");
    const keys = seenKeys();
    const hasAssistant = draft.roles.some((r) => r.target === "assistant");
    const roleWords: Record<string, MsgKey> = {
      user: "roleUser",
      assistant: "roleAssistant",
      system: "roleSystem",
      tool: "roleTool",
    };
    const metaWords: Record<MetadataSlot, MsgKey> = {
      language: "metaLanguage",
      task_category: "metaTaskCategory",
      consent: "metaConsent",
      quality_label: "metaQuality",
      source_record_id: "metaSourceId",
      event_timestamp: "metaTimestamp",
    };
    const setRole = (i: number, patch: Partial<RoleRow>) =>
      update({ roles: draft.roles.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
    return (
      <section aria-labelledby={`${uid}-h-map`} data-testid="mapping-step-map">
        <h3 id={`${uid}-h-map`}>{tr("stepMap", locale)}</h3>
        {probe.state === "ok" ? probeView(probe.data) : null}
        {!tabular && keys.length > 0 ? (
          <p data-testid="keys-seen">
            {tr("keysSeen", locale)}{" "}
            {keys.map((k, i) => (
              <span key={k}>
                {i > 0 ? (locale === "ar" ? "، " : ", ") : null}
                <bdi dir="auto">{k}</bdi>
              </span>
            ))}
          </p>
        ) : null}
        <fieldset
          id={`${uid}-shape`}
          tabIndex={-1}
          aria-invalid={err ? true : undefined}
          aria-describedby={err ? `${uid}-shape-error` : undefined}
        >
          <legend>
            {tr("shapeLegend", locale)} <span>{phraseRequired(locale)}</span>
          </legend>
          {available.map(([s, label]) => (
            <label
              key={s}
              style={{
                display: "flex",
                gap: "var(--space-2)",
                alignItems: "center",
                minBlockSize: "var(--target-min)",
              }}
            >
              <input
                type="radio"
                name={`${uid}-shape`}
                value={s}
                checked={draft.shape === s}
                onChange={() => update({ shape: s })}
                style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
              />
              {tr(label, locale)}
            </label>
          ))}
          {err ? (
            <div id={`${uid}-shape-error`} role="alert">
              {err}
            </div>
          ) : null}
        </fieldset>
        {draft.shape === "prompt_completion" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: "var(--space-3)",
            }}
          >
            {SlotInput({
              field: "prompt",
              label: tr("promptLabel", locale),
              value: draft.prompt,
              onChange: (v) => update({ prompt: v }),
              required: true,
            })}
            {SlotInput({
              field: "completion",
              label: tr("completionLabel", locale),
              value: draft.completion,
              onChange: (v) => update({ completion: v }),
              required: true,
            })}
            {SlotInput({
              field: "system",
              label: tr("systemLabel", locale),
              value: draft.system,
              onChange: (v) => update({ system: v }),
              required: false,
            })}
          </div>
        ) : null}
        {draft.shape === "messages" || draft.shape === "messages_json" ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr)",
              gap: "var(--space-3)",
            }}
          >
            {draft.shape === "messages" ? (
              <Field
                id={`${uid}-messagesPointer`}
                label={tr("messagesPointerLabel", locale)}
                helper={tr("messagesPointerHelp", locale)}
                requirement="required"
                locale={locale}
                dir="ltr"
                value={draft.messagesPointer}
                error={fieldError("messagesPointer")}
                maxLength={256}
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => update({ messagesPointer: e.target.value })}
              />
            ) : (
              SlotInput({
                field: "messagesSlot",
                label: tr("messagesColumnLabel", locale),
                value: draft.messagesSlot,
                onChange: (v) => update({ messagesSlot: v }),
                required: true,
              })
            )}
            {(["roleKey", "contentKey"] as const).map((f) => (
              <Field
                key={f}
                id={`${uid}-${f}`}
                label={tr(f === "roleKey" ? "roleKeyLabel" : "contentKeyLabel", locale)}
                helper={tr("keyHelp", locale)}
                requirement="required"
                locale={locale}
                dir="auto"
                value={draft[f]}
                error={fieldError(f)}
                maxLength={256}
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => update({ [f]: e.target.value } as Partial<Draft>)}
              />
            ))}
            <fieldset
              id={`${uid}-roles`}
              tabIndex={-1}
              aria-describedby={`${uid}-roles-help${hasAssistant ? "" : ` ${uid}-roles-warning`}`}
            >
              <legend>
                {tr("roleMapLegend", locale)} <span>{phraseRequired(locale)}</span>
              </legend>
              <p id={`${uid}-roles-help`}>{tr("roleMapHelp", locale)}</p>
              {draft.roles.map((r, i) => {
                const rowErr = fieldError(`role-${i}`);
                return (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "var(--space-2)",
                      alignItems: "end",
                      marginBlockEnd: "var(--space-2)",
                    }}
                  >
                    <Field
                      id={`${uid}-role-${i}`}
                      label={tr("rawRoleLabel", locale, { n: i + 1 })}
                      requirement="required"
                      locale={locale}
                      dir="auto"
                      value={r.raw}
                      error={rowErr}
                      maxLength={256}
                      spellCheck={false}
                      autoComplete="off"
                      onChange={(e) => setRole(i, { raw: e.target.value })}
                    />
                    <div>
                      <label
                        htmlFor={`${uid}-role-${i}-target`}
                        style={{ display: "block", marginBlockEnd: "var(--space-1)" }}
                      >
                        {tr("roleTargetLabel", locale, { n: i + 1 })}{" "}
                        <span>{phraseRequired(locale)}</span>
                      </label>
                      <select
                        id={`${uid}-role-${i}-target`}
                        value={r.target}
                        aria-required
                        onChange={(e) =>
                          setRole(i, { target: e.target.value as RoleRow["target"] })
                        }
                        style={{ minBlockSize: "var(--target-min)" }}
                      >
                        <option value="">{tr("notMappedOption", locale)}</option>
                        {ROLE_TARGETS.map((t) => (
                          <option key={t} value={t}>
                            {tr(roleWords[t]!, locale)}
                          </option>
                        ))}
                      </select>
                    </div>
                    {draft.roles.length > 1 ? (
                      <Button
                        onClick={() => update({ roles: draft.roles.filter((_, j) => j !== i) })}
                      >
                        {tr("removeRole", locale, { n: i + 1 })}
                      </Button>
                    ) : null}
                  </div>
                );
              })}
              <Button
                onClick={() => update({ roles: [...draft.roles, { raw: "", target: "" }] })}
                data-testid="add-role"
              >
                {tr("addRole", locale)}
              </Button>
              {!hasAssistant ? (
                <p id={`${uid}-roles-warning`} data-testid="no-assistant-warning">
                  <span aria-hidden="true">! </span>
                  {tr("noAssistantWarning", locale)}
                </p>
              ) : null}
            </fieldset>
            <fieldset>
              <legend>
                {tr("unmappedLegend", locale)} <span>{phraseRequired(locale)}</span>
              </legend>
              {(["reject", "ignore"] as const).map((v) => (
                <label
                  key={v}
                  style={{
                    display: "flex",
                    gap: "var(--space-2)",
                    alignItems: "center",
                    minBlockSize: "var(--target-min)",
                  }}
                >
                  <input
                    type="radio"
                    name={`${uid}-unmapped`}
                    value={v}
                    checked={draft.onUnmapped === v}
                    onChange={() => update({ onUnmapped: v })}
                    style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
                  />
                  {tr(v === "reject" ? "unmappedReject" : "unmappedIgnore", locale)}
                </label>
              ))}
            </fieldset>
            {(["toolCallsKey", "toolCallIdKey", "nameKey"] as const).map((f) => (
              <Field
                key={f}
                id={`${uid}-${f}`}
                label={tr(
                  f === "toolCallsKey"
                    ? "toolCallsKeyLabel"
                    : f === "toolCallIdKey"
                      ? "toolCallIdKeyLabel"
                      : "nameKeyLabel",
                  locale,
                )}
                requirement="optional"
                locale={locale}
                dir="auto"
                value={draft[f]}
                maxLength={256}
                spellCheck={false}
                autoComplete="off"
                onChange={(e) => update({ [f]: e.target.value } as Partial<Draft>)}
              />
            ))}
          </div>
        ) : null}
        {draft.shape !== "" ? (
          <fieldset style={{ marginBlockStart: "var(--space-3)" }}>
            <legend>{tr("metadataLegend", locale)}</legend>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "minmax(0, 1fr)",
                gap: "var(--space-3)",
              }}
            >
              {SlotInput({
                field: "groupKey",
                label: tr("groupKeyLabel", locale),
                value: draft.groupKey,
                onChange: (v) => update({ groupKey: v }),
                required: false,
              })}
              {METADATA_SLOTS.map((name) => (
                <div key={name}>
                  {SlotInput({
                    field: `meta-${name}`,
                    label: tr(metaWords[name], locale),
                    value: draft.metadata[name],
                    onChange: (v) => update({ metadata: { ...draft.metadata, [name]: v } }),
                    required: false,
                  })}
                </div>
              ))}
            </div>
          </fieldset>
        ) : null}
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginBlockStart: "var(--space-3)",
          }}
        >
          <Button onClick={() => setStep(1)}>{tr("back", locale)}</Button>
          <Button
            variant="primary"
            data-testid="preview-button"
            onClick={() => {
              if (document_) setStep(3);
              void runPreview();
            }}
          >
            {tr("previewButton", locale)}
          </Button>
        </div>
      </section>
    );
  }

  function previewView(p: Preview, roleMap: Record<string, Json> | null) {
    const rejected = p.counts.rejected ?? 0;
    const extraCounts = Object.entries(p.counts).filter(
      ([k]) => !["seen", "accepted", "rejected"].includes(k),
    );
    return (
      <div data-testid="preview-result">
        {p.masked ? <p data-testid="preview-masked-notice">{tr("maskedNotice", locale)}</p> : null}
        {p.state !== "completed" ? (
          <AlertMessage
            error={uiError(
              `${tr("previewFailedState", locale)}${p.failureCode ? ` ${errorText(p.failureCode, locale) ?? ""} (${tr("codeLabel", locale)}: ${p.failureCode})` : ""}`,
              "input",
            )}
            locale={locale}
            testId="preview-failed"
          />
        ) : null}
        {!p.complete ? <p data-testid="preview-partial">{tr("previewPartial", locale)}</p> : null}
        {rejected > 0 ? (
          <p data-testid="preview-rejections">
            {tr("previewHasRejections", locale, { n: rejected })}
          </p>
        ) : null}
        <h4>{tr("countsHeading", locale)}</h4>
        <dl
          data-testid="preview-counts"
          style={{
            display: "grid",
            gridTemplateColumns: "max-content 1fr",
            gap: "var(--space-1) var(--space-3)",
          }}
        >
          <dt>{tr("countSeen", locale)}</dt>
          <dd style={{ margin: 0 }}>{p.counts.seen ?? 0}</dd>
          <dt>{tr("countAccepted", locale)}</dt>
          <dd style={{ margin: 0 }}>{p.counts.accepted ?? 0}</dd>
          <dt>{tr("countRejected", locale)}</dt>
          <dd style={{ margin: 0 }}>{rejected}</dd>
          {extraCounts.map(([k, n]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>
                <bdi>{tr("countOther", locale, { code: k.replace(/^rejected:/, "") })}</bdi>
              </dt>
              <dd style={{ margin: 0 }}>{n}</dd>
            </div>
          ))}
        </dl>
        {p.observedRoleValues.length > 0 ? (
          <div data-testid="observed-roles">
            <h4>{tr("rolesSeen", locale)}</h4>
            <ul>
              {p.observedRoleValues.map((v, i) => {
                const unmapped =
                  typeof v === "string" && roleMap !== null && !Object.hasOwn(roleMap, v);
                return (
                  <li key={i} data-unmapped={unmapped ? "true" : undefined}>
                    <NameView value={v} locale={locale} />
                    {unmapped ? (
                      <>
                        {" — "}
                        <span aria-hidden="true">! </span>
                        {tr("roleNotInMap", locale)}
                      </>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
        {p.rows.length === 0 ? <p>{tr("noPreviewRows", locale)}</p> : null}
        <ol
          style={{
            listStyle: "none",
            padding: 0,
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr)",
            gap: "var(--space-4)",
          }}
        >
          {p.rows.map((row, i) => (
            <li key={i}>
              <article
                aria-labelledby={`${uid}-row-${i}`}
                data-testid={`preview-row-${i}`}
                data-outcome={row.outcome}
                style={{
                  border: "1px solid var(--color-border)",
                  padding: "var(--space-3)",
                  minInlineSize: 0,
                }}
              >
                <h5 id={`${uid}-row-${i}`} style={{ margin: 0 }}>
                  <bdi dir="auto">{tr("rowHeading", locale, { locator: row.locator })}</bdi>
                </h5>
                <p data-testid={`preview-row-${i}-outcome`}>
                  {row.outcome === "accepted" ? (
                    <span style={{ color: "var(--color-success)" }}>
                      <span aria-hidden="true">✔ </span>
                      {tr("accepted", locale)}
                    </span>
                  ) : (
                    <span style={{ color: "var(--color-danger)" }}>
                      <span aria-hidden="true">✗ </span>
                      {tr("rejected", locale)}
                      {": "}
                      {row.code && Object.hasOwn(ROW_CODE_MESSAGES, row.code)
                        ? locale === "ar"
                          ? ROW_CODE_MESSAGES[row.code]!.ar
                          : ROW_CODE_MESSAGES[row.code]!.en
                        : null}{" "}
                      ({tr("codeLabel", locale)}:{" "}
                      <code data-testid={`preview-row-${i}-code`}>{row.code}</code>
                      {row.slot ? (
                        <>
                          {", "}
                          {tr("slotLabel", locale)}: <bdi dir="auto">{row.slot}</bdi>
                        </>
                      ) : null}
                      )
                    </span>
                  )}
                </p>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 16rem), 1fr))",
                    gap: "var(--space-3)",
                  }}
                >
                  <section
                    aria-label={`${tr("rawHeading", locale)} ${row.locator}`}
                    style={{ minInlineSize: 0 }}
                  >
                    <h6 style={{ margin: 0 }}>{tr("rawHeading", locale)}</h6>
                    <ValueView value={row.rawRow} locale={locale} />
                  </section>
                  <section
                    aria-label={`${tr("canonicalHeading", locale)} ${row.locator}`}
                    style={{ minInlineSize: 0 }}
                  >
                    <h6 style={{ margin: 0 }}>{tr("canonicalHeading", locale)}</h6>
                    {row.candidate ? (
                      <ValueView value={row.candidate} locale={locale} />
                    ) : (
                      <p>{tr("noCanonical", locale)}</p>
                    )}
                  </section>
                </div>
                {row.notMapped.length > 0 ? (
                  <p>
                    {tr("notMapped", locale)}
                    {row.notMapped.map((n, j) => (
                      <span key={j}>
                        {j > 0 ? (locale === "ar" ? "، " : ", ") : null}
                        <NameView value={n} locale={locale} />
                      </span>
                    ))}
                  </p>
                ) : null}
                {row.comparison.length > 0 ? (
                  <div
                    role="region"
                    aria-label={`${tr("comparisonCaption", locale)} ${row.locator}`}
                    tabIndex={0}
                    style={{ overflowX: "auto" }}
                  >
                    <table style={{ fontSize: "var(--font-table)" }}>
                      <caption>{tr("comparisonCaption", locale)}</caption>
                      <thead>
                        <tr>
                          <th scope="col">{tr("colSlot", locale)}</th>
                          <th scope="col">{tr("colRawType", locale)}</th>
                          <th scope="col">{tr("colCanonicalType", locale)}</th>
                          <th scope="col">{tr("colIdentical", locale)}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {row.comparison.map((c, j) => (
                          <tr key={j}>
                            <td>
                              <bdi dir="auto">{c.slot}</bdi>
                            </td>
                            <td>
                              <bdi>{c.rawType}</bdi>
                            </td>
                            <td>
                              <bdi>{c.canonicalType}</bdi>
                            </td>
                            <td>{tr(c.identical ? "yes" : "no", locale)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </article>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  function previewSection() {
    const roleMap =
      document_ && isObj(document_.role_map) ? (document_.role_map as Record<string, Json>) : null;
    return (
      <section aria-labelledby={`${uid}-h-preview`} data-testid="mapping-step-preview">
        <h3 id={`${uid}-h-preview`}>{tr("previewHeading", locale)}</h3>
        {preview.state === "loading" ? (
          <p role="status" aria-busy="true" data-testid="preview-loading">
            {tr("previewing", locale)}
          </p>
        ) : null}
        {preview.state === "error" ? (
          <div data-testid="preview-error">
            <AlertMessage error={preview.error} locale={locale} testId="preview-alert" />
          </div>
        ) : null}
        {preview.state === "ok" && !previewCurrent ? (
          <p role="status" data-testid="preview-stale">
            {tr("previewStale", locale)}
          </p>
        ) : null}
        {preview.state === "ok" ? previewView(preview.data, roleMap) : null}
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginBlockStart: "var(--space-3)",
          }}
        >
          <Button onClick={() => setStep(2)}>{tr("back", locale)}</Button>
          <Button
            onClick={() => void runPreview()}
            data-testid="preview-again"
            disabled={preview.state === "loading"}
          >
            {tr("previewButton", locale)}
          </Button>
          <Button
            variant="primary"
            data-testid="to-propose"
            disabledReason={previewCurrent ? undefined : tr("needPreviewFirst", locale)}
            onClick={() => setStep(4)}
          >
            {tr("continue", locale)}
          </Button>
        </div>
      </section>
    );
  }

  function summaryRows(doc: Record<string, Json>): ReactNode {
    const shapeWord: Record<string, MsgKey> = {
      messages: "shapeMessages",
      messages_json: "shapeMessagesJson",
      prompt_completion: "shapePromptCompletion",
    };
    const fields = Object.entries(doc).filter(
      ([k]) => !["shape", "csv", "json", "xlsx"].includes(k),
    );
    return (
      <dl data-testid="mapping-summary" style={{ overflowWrap: "anywhere" }}>
        <dt>{tr("summaryShape", locale)}</dt>
        <dd>
          {isStr(doc.shape) && shapeWord[doc.shape] ? (
            tr(shapeWord[doc.shape]!, locale)
          ) : (
            <bdi>{String(doc.shape)}</bdi>
          )}
        </dd>
        {doc.csv || doc.json || doc.xlsx ? (
          <>
            <dt>{tr("summaryFormat", locale)}</dt>
            <dd>
              <ValueView value={(doc.csv ?? doc.json ?? doc.xlsx) as Json} locale={locale} />
            </dd>
          </>
        ) : null}
        <dt>{tr("summaryFields", locale)}</dt>
        <dd>
          <ValueView value={Object.fromEntries(fields)} locale={locale} />
        </dd>
      </dl>
    );
  }

  function proposeSection() {
    return (
      <section aria-labelledby={`${uid}-h-propose`} data-testid="mapping-step-propose">
        <h3 id={`${uid}-h-propose`}>{tr("summaryHeading", locale)}</h3>
        {document_ ? summaryRows(document_) : null}
        <div ref={errorRef}>
          {saveError ? (
            <AlertMessage error={saveError} locale={locale} testId="save-error" focusable />
          ) : null}
        </div>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginBlockStart: "var(--space-3)",
          }}
        >
          <Button onClick={() => setStep(3)}>{tr("back", locale)}</Button>
          <Button
            variant="primary"
            data-testid="save-mapping"
            disabled={saving}
            disabledReason={previewCurrent ? undefined : tr("needPreviewFirst", locale)}
            onClick={() => void save()}
          >
            {tr(saving ? "saving" : "saveMapping", locale)}
          </Button>
        </div>
      </section>
    );
  }

  function approveSection(m: MappingRow) {
    const purpose = purposeText(m.datasetPurpose, locale);
    const decided = m.status !== "proposed";
    return (
      <section aria-labelledby={`${uid}-h-approve`} data-testid="mapping-step-approve">
        <h3 id={`${uid}-h-approve`}>{tr("approveHeading", locale, { n: m.mappingVersion })}</h3>
        <dl style={{ overflowWrap: "anywhere" }}>
          <dt>{tr("colStatus", locale)}</dt>
          <dd data-testid="mapping-status">
            <StatusBadge state={m.status === "proposed" ? "draft" : m.status} locale={locale} />
          </dd>
          <dt id={`${uid}-purpose-label`}>{tr("purposeLabel", locale)}</dt>
          <dd aria-describedby={`${uid}-purpose-help`} data-testid="mapping-purpose">
            {purpose}
            <div id={`${uid}-purpose-help`}>{tr("purposeHelp", locale)}</div>
          </dd>
          <dt>{tr("allowedPurposes", locale)}</dt>
          <dd data-testid="mapping-allowed-purposes">
            {m.allowedPurposes
              .map((p) => purposeText(p, locale))
              .join(locale === "ar" ? "، " : ", ")}
          </dd>
          <dt>{tr("mappingDigest", locale)}</dt>
          <dd>
            <code style={{ overflowWrap: "anywhere" }} data-testid="mapping-sha">
              {m.mappingSha256}
            </code>
          </dd>
          {m.decision ? (
            <>
              <dt>{tr("decidedBy", locale)}</dt>
              <dd>
                <bdi dir="auto">{m.decision.decidedBy}</bdi>
              </dd>
            </>
          ) : null}
          {m.approvedAt ? (
            <>
              <dt>{tr("decidedAt", locale)}</dt>
              <dd>
                <bdi>{formatTime(m.approvedAt, lang)}</bdi>
              </dd>
            </>
          ) : null}
        </dl>
        {m.mappingDocument ? (
          summaryRows(m.mappingDocument)
        ) : (
          <p data-testid="mapping-hidden">{tr("hiddenDocument", locale)}</p>
        )}
        {!decided ? (
          <>
            {preview.state === "loading" ? (
              <p role="status" aria-busy="true">
                {tr("previewing", locale)}
              </p>
            ) : null}
            {preview.state === "error" ? (
              <AlertMessage error={preview.error} locale={locale} testId="preview-alert" />
            ) : null}
            {!decisionPreviewMatches ? (
              <div data-testid="decision-needs-preview">
                {preview.state === "ok" ? <p>{tr("previewMismatch", locale)}</p> : null}
                <Button
                  onClick={() => void runPreview(m.mappingDocument)}
                  disabled={preview.state === "loading" || m.mappingDocument === null}
                  data-testid="preview-version"
                >
                  {tr("previewAgain", locale)}
                </Button>
              </div>
            ) : null}
            {decisionPreviewMatches && preview.state === "ok"
              ? previewView(
                  preview.data,
                  isObj(m.mappingDocument?.role_map)
                    ? (m.mappingDocument!.role_map as Record<string, Json>)
                    : null,
                )
              : null}
            {ignored.length > 0 && decisionPreviewMatches ? (
              <fieldset aria-describedby={`${uid}-ack-help`} data-testid="ignored-keys">
                <legend>
                  {tr("ignoredKeysLegend", locale)} <span>{phraseRequired(locale)}</span>
                </legend>
                <p id={`${uid}-ack-help`}>{tr("ignoredKeysHelp", locale)}</p>
                {ignored.map((k, i) => (
                  <label
                    key={k}
                    style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}
                  >
                    <input
                      type="checkbox"
                      id={`${uid}-ack-${i}`}
                      checked={acked.includes(k)}
                      onChange={(e) =>
                        setAcked((a) => (e.target.checked ? [...a, k] : a.filter((x) => x !== k)))
                      }
                      style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
                    />
                    <span>
                      {tr("ackKey", locale)} <bdi dir="auto">{k}</bdi>
                    </span>
                  </label>
                ))}
              </fieldset>
            ) : null}
            <div>
              <label
                htmlFor={`${uid}-rationale`}
                style={{ display: "block", marginBlockEnd: "var(--space-1)" }}
              >
                {tr("rationaleLabel", locale)} <span>{phraseRequired(locale)}</span>
              </label>
              <div id={`${uid}-rationale-help`}>{tr("rationaleHelp", locale)}</div>
              <textarea
                id={`${uid}-rationale`}
                value={rationale}
                dir="auto"
                rows={3}
                aria-required
                aria-invalid={fieldError("rationale") ? true : undefined}
                aria-describedby={`${uid}-rationale-help ${uid}-rationale-count${fieldError("rationale") ? ` ${uid}-rationale-error` : ""}`}
                onChange={(e) => setRationale(e.target.value)}
                style={{ inlineSize: "100%", maxInlineSize: "40rem", boxSizing: "border-box" }}
              />
              <div id={`${uid}-rationale-count`}>
                {tr("rationaleCount", locale, { n: rationale.length })}
              </div>
              {fieldError("rationale") ? (
                <div id={`${uid}-rationale-error`} role="alert">
                  {fieldError("rationale")}
                </div>
              ) : null}
            </div>
            <label style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
              <input
                type="checkbox"
                id={`${uid}-confirm`}
                data-testid="approve-confirm"
                checked={confirmed}
                aria-invalid={fieldError("confirm") ? true : undefined}
                onChange={(e) => setConfirmed(e.target.checked)}
                style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
              />
              {tr("confirmLabel", locale, { purpose })}
            </label>
            <div ref={errorRef}>
              {decisionError ? (
                <AlertMessage
                  error={decisionError}
                  locale={locale}
                  testId="decision-error"
                  focusable
                />
              ) : null}
            </div>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "var(--space-2)",
                marginBlockStart: "var(--space-3)",
              }}
            >
              <Button onClick={startNew}>{tr("back", locale)}</Button>
              <Button
                variant="primary"
                data-testid="approve-button"
                disabled={deciding !== ""}
                disabledReason={decisionPreviewMatches ? undefined : tr("needPreviewFirst", locale)}
                onClick={() => void decide("approve")}
              >
                {tr(deciding === "approve" ? "approving" : "approveButton", locale, { purpose })}
              </Button>
              <Button
                variant="danger"
                data-testid="reject-button"
                disabled={deciding !== ""}
                onClick={(e) => {
                  const found = decisionProblems("reject");
                  setProblems(found);
                  if (found.length > 0) {
                    setSubmitCount((n) => n + 1);
                    return;
                  }
                  rejectOpener.current = e.currentTarget;
                  setRejectOpen(true);
                }}
              >
                {tr(deciding === "reject" ? "rejecting" : "rejectButton", locale)}
              </Button>
            </div>
            <Dialog
              open={rejectOpen}
              onOpenChange={setRejectOpen}
              title={tr("rejectTitle", locale, { n: m.mappingVersion })}
              description={tr("rejectBody", locale)}
              locale={locale}
              data-testid="reject-dialog"
              restoreFocusTo={rejectOpener.current}
            >
              <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
                <Button onClick={() => setRejectOpen(false)}>{tr("cancel", locale)}</Button>
                <Button
                  variant="danger"
                  data-testid="confirm-reject"
                  disabled={deciding !== ""}
                  onClick={() => void decide("reject")}
                >
                  {tr("confirmReject", locale)}
                </Button>
              </div>
            </Dialog>
          </>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
            <div ref={errorRef}>
              {decisionError ? (
                <AlertMessage
                  error={decisionError}
                  locale={locale}
                  testId="decision-error"
                  focusable
                />
              ) : null}
            </div>
            <Button onClick={startNew} data-testid="new-version">
              {tr("newVersion", locale)}
            </Button>
          </div>
        )}
      </section>
    );
  }

  const headingId = `${uid}-title`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid="dataset-mapping-wizard"
      lang={lang}
      dir={dir}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr)",
        gap: "var(--space-4)",
        minInlineSize: 0,
        overflowWrap: "anywhere",
      }}
    >
      <h2 id={headingId}>{tr("title", locale)}</h2>
      <p data-testid="mapping-state" aria-label={tr("stateLabel", locale)}>
        <StatusBadge state={status.state} detail={status.detail} locale={locale} />
      </p>
      <p
        ref={noticeRef}
        role="status"
        tabIndex={-1}
        data-testid="mapping-notice"
        style={
          notice
            ? undefined
            : {
                position: "absolute",
                inlineSize: 1,
                blockSize: 1,
                overflow: "hidden",
                clipPath: "inset(50%)",
              }
        }
      >
        {notice}
      </p>
      <section aria-labelledby={`${uid}-h-versions`}>
        <h3 id={`${uid}-h-versions`}>{tr("versionsHeading", locale)}</h3>
        {versionsSection()}
      </section>
      <nav aria-label={tr("stepsLabel", locale)}>
        <p>{tr("stepOf", locale, { n: step })}</p>
        <ol
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            listStyle: "none",
            padding: 0,
          }}
        >
          {stepNames.map((name, i) => {
            const s = (i + 1) as Step;
            const current = s === step;
            const can = reachable(s);
            return (
              <li key={name}>
                {current ? (
                  <span
                    aria-current="step"
                    style={{
                      fontWeight: 700,
                      display: "inline-block",
                      paddingBlock: "var(--space-1)",
                    }}
                  >
                    {i + 1}. {tr(name, locale)} ({tr("stepCurrent", locale)})
                  </span>
                ) : can ? (
                  <Button onClick={() => setStep(s)} data-testid={`step-${s}`}>
                    {i + 1}. {tr(name, locale)}
                  </Button>
                ) : (
                  <span
                    style={{
                      color: "var(--color-muted)",
                      display: "inline-block",
                      paddingBlock: "var(--space-1)",
                    }}
                  >
                    {i + 1}. {tr(name, locale)} ({tr("stepLocked", locale)})
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      </nav>
      <ErrorSummary errors={problems} locale={locale} submitCount={submitCount} />
      {step === 1 ? formatSection() : null}
      {step === 2 ? mapSection() : null}
      {step === 3 ? previewSection() : null}
      {step === 4 ? proposeSection() : null}
      {step === 5 && mapping ? approveSection(mapping) : null}
    </section>
  );
}

function phraseRequired(locale: Locale): string {
  return phrase("fieldRequired", locale);
}
function phraseOptional(locale: Locale): string {
  return phrase("fieldOptional", locale);
}

function formatTime(iso: string, lang: Locale): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return (
    new Date(t).toLocaleString(lang === "ar" ? "ar" : "en", {
      timeZone: "UTC",
      dateStyle: "medium",
      timeStyle: "short",
    }) + " UTC"
  );
}
