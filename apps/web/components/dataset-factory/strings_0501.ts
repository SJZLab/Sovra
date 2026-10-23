/**
 * FTF-0501-UI (resumable dataset upload) en/ar catalogue (FTF-UX02 round 3, finding 4). Every
 * system string the screen shows lives here, including every typed failure message. The English
 * value of each entry is byte-for-byte what the pre-round-3 component rendered, so the certified
 * `ftf_0501_ui.spec.ts` suite keeps passing unchanged at the default `locale="en"`. File names,
 * ids and MIME types are DATA and are shown as received, isolated LTR.
 */

import { resolveLocale, type Locale } from "../ui";

export type CatalogueEntry = { readonly en: string; readonly ar: string };

const STRINGS = {
  rootLabel: { en: "Resumable dataset upload", ar: "رفع مجموعة بيانات قابل للاستئناف" },
  heading: { en: "Upload a dataset", ar: "رفع مجموعة بيانات" },
  purposeLabel: { en: "Purpose", ar: "الغرض" },
  contentTypeLabel: { en: "Content type", ar: "نوع المحتوى" },
  fileLabel: { en: "File", ar: "الملف" },
  fileHelper: {
    en: "Choose the file to upload. It is split into parts and uploaded straight to storage.",
    ar: "اختر الملف المراد رفعه. يُقسَّم إلى أجزاء ويُرفع مباشرة إلى التخزين.",
  },
  resumeFileLabel: { en: "Choose the original file", ar: "اختر الملف الأصلي" },
  startUpload: { en: "Start upload", ar: "بدء الرفع" },
  startUploadNeedsFile: { en: "Choose a file to start.", ar: "اختر ملفًا للبدء." },
  progress: {
    en: "Uploaded {count} of {total} parts.",
    ar: "تم رفع {count} من {total} من الأجزاء.",
  },
  interrupted: {
    en: "Upload interrupted after {count} of {total} parts. The session is still open; resuming only re-uploads the parts storage does not already have.",
    ar: "توقف الرفع بعد {count} من {total} من الأجزاء. لا تزال الجلسة مفتوحة؛ يعيد الاستئناف رفع الأجزاء غير الموجودة في التخزين فقط.",
  },
  resumeButton: { en: "Resume upload", ar: "استئناف الرفع" },
  allUploaded: { en: "All parts uploaded.", ar: "تم رفع كل الأجزاء." },
  recheck: {
    en: "Re-check uploaded parts and resume",
    ar: "إعادة فحص الأجزاء المرفوعة والاستئناف",
  },
  datasetNameLabel: { en: "Dataset name", ar: "اسم مجموعة البيانات" },
  datasetNameHelper: {
    en: "The name the dataset is created with. A name and version can be used once.",
    ar: "الاسم الذي تُنشأ به مجموعة البيانات. يمكن استخدام الاسم والإصدار مرة واحدة.",
  },
  finalizeButton: { en: "Finalize", ar: "إنهاء" },
  finalizeNeedsName: {
    en: "Enter a dataset name to finalize.",
    ar: "أدخل اسم مجموعة البيانات للإنهاء.",
  },
  finalizing: { en: "Finalizing...", ar: "جارٍ الإنهاء..." },
  created: { en: "Dataset created.", ar: "تم إنشاء مجموعة البيانات." },
  alreadyVerified: {
    en: "This upload session was already finalized and verified; its Dataset exists. Nothing more to upload.",
    ar: "تم إنهاء جلسة الرفع هذه والتحقق منها بالفعل؛ مجموعة البيانات موجودة. لا شيء آخر للرفع.",
  },
  checkStatus: { en: "Check upload status", ar: "فحص حالة الرفع" },
  newUpload: { en: "Start a new upload", ar: "ابدأ رفعًا جديدًا" },
  uploadAnother: { en: "Upload another dataset", ar: "ارفع مجموعة بيانات أخرى" },
  back: { en: "Back", ar: "رجوع" },

  // failures / messages (English verbatim from the pre-round-3 component)
  malformed: {
    en: "The server's reply could not be verified against what was requested, so it was not used.",
    ar: "تعذّر التحقق من رد الخادم مقابل ما طُلب، لذلك لم يُستخدم.",
  },
  requestFailed: {
    en: "request failed ({status}); this can be retried",
    ar: "فشل الطلب ({status})؛ يمكن إعادة المحاولة",
  },
  partFailed: {
    en: "part {n} upload failed ({status})",
    ar: "فشل رفع الجزء {n} ({status})",
  },
  notEveryPart: { en: "not every part was uploaded", ar: "لم يتم رفع كل الأجزاء" },
  chooseFile: { en: "choose a file first", ar: "اختر ملفًا أولًا" },
  rejected: { en: "rejected", ar: "مرفوض" },
  rejectedReason: { en: "rejected: {reason}", ar: "مرفوض: {reason}" },
  fileMismatch: {
    en: "the selected file does not match the upload in progress; choose the original file, or abandon this draft to start a new upload",
    ar: "الملف المحدد لا يطابق الرفع الجاري؛ اختر الملف الأصلي، أو تجاهل هذه المسودة لبدء رفع جديد",
  },
  expiredDefault: { en: "This upload session expired.", ar: "انتهت صلاحية جلسة الرفع هذه." },

  upload_session_not_open: {
    en: "This upload session is no longer open.",
    ar: "جلسة الرفع هذه لم تعد مفتوحة.",
  },
  upload_incomplete: {
    en: "Not every part has been uploaded yet.",
    ar: "لم يتم رفع كل الأجزاء بعد.",
  },
  dataset_version_conflict: {
    en: "A dataset with this name and version already exists; choose another name. The upload is kept.",
    ar: "توجد مجموعة بيانات بهذا الاسم والإصدار؛ اختر اسمًا آخر. يتم الاحتفاظ بالرفع.",
  },
  upload_expired: {
    en: "This upload session expired before it was finalized.",
    ar: "انتهت صلاحية جلسة الرفع قبل إنهائها.",
  },
  size_mismatch: {
    en: "The uploaded bytes do not match the declared size; nothing was created.",
    ar: "البايتات المرفوعة لا تطابق الحجم المعلن؛ لم يتم إنشاء أي شيء.",
  },
  sha256_mismatch: {
    en: "The uploaded bytes do not match the declared SHA-256; nothing was created.",
    ar: "البايتات المرفوعة لا تطابق SHA-256 المعلن؛ لم يتم إنشاء أي شيء.",
  },
  content_type_mismatch: {
    en: "The uploaded content does not match the declared type; nothing was created.",
    ar: "المحتوى المرفوع لا يطابق النوع المعلن؛ لم يتم إنشاء أي شيء.",
  },
  part_size_mismatch: {
    en: "One or more uploaded parts have the wrong size; nothing was created.",
    ar: "حجم جزء مرفوع واحد أو أكثر غير صحيح؛ لم يتم إنشاء أي شيء.",
  },
  unsafe_archive: {
    en: "The uploaded spreadsheet archive was refused as unsafe; nothing was created.",
    ar: "رُفض أرشيف جدول البيانات المرفوع لأنه غير آمن؛ لم يتم إنشاء أي شيء.",
  },
  unexpected_part: {
    en: "An unexpected part was found in storage; nothing was created.",
    ar: "عُثر على جزء غير متوقع في التخزين؛ لم يتم إنشاء أي شيء.",
  },
  unsupported_content_type: {
    en: "The declared content type is not supported; nothing was created.",
    ar: "نوع المحتوى المعلن غير مدعوم؛ لم يتم إنشاء أي شيء.",
  },
  source_revoked: {
    en: "The dataset source connection no longer grants this purpose; nothing was created.",
    ar: "لم يعد اتصال مصدر البيانات يمنح هذا الغرض؛ لم يتم إنشاء أي شيء.",
  },
  verification_mismatch: {
    en: "Verification failed; nothing was created.",
    ar: "فشل التحقق؛ لم يتم إنشاء أي شيء.",
  },
  storage_unavailable: {
    en: "Storage is temporarily unavailable; this upload can be retried.",
    ar: "التخزين غير متاح مؤقتًا؛ يمكن إعادة محاولة هذا الرفع.",
  },
  storage_not_configured: {
    en: "Storage is not configured; this upload can be retried once it is.",
    ar: "التخزين غير مهيأ؛ يمكن إعادة محاولة هذا الرفع بعد تهيئته.",
  },
  invalid_body: { en: "The request was rejected as malformed.", ar: "رُفض الطلب لأنه غير صالح." },
  invalid_part_numbers: {
    en: "The requested part numbers were rejected.",
    ar: "رُفضت أرقام الأجزاء المطلوبة.",
  },
  not_found: { en: "The upload session was not found.", ar: "لم يتم العثور على جلسة الرفع." },
  purpose_forbidden: {
    en: "Your role may not upload for this purpose.",
    ar: "لا يسمح دورك بالرفع لهذا الغرض.",
  },
  purpose_not_granted: {
    en: "The dataset source connection does not grant this purpose.",
    ar: "اتصال مصدر مجموعة البيانات لا يمنح هذا الغرض.",
  },
  not_an_upload_source: {
    en: "The dataset source connection is not an upload source.",
    ar: "اتصال مصدر مجموعة البيانات ليس مصدر رفع.",
  },
  unsupported_media_type: {
    en: "The declared content type is not supported.",
    ar: "نوع المحتوى المعلن غير مدعوم.",
  },
} as const satisfies Record<string, CatalogueEntry>;

export type Key0501 = keyof typeof STRINGS;
export const STRINGS_0501: Record<Key0501, CatalogueEntry> = STRINGS;
export const KEYS_0501 = Object.keys(STRINGS) as Key0501[];

function fill(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

export function t0501(
  key: Key0501,
  locale: Locale = "en",
  vars?: Record<string, string | number>,
): string {
  return fill(STRINGS[key][resolveLocale(locale)], vars);
}

/** The server error codes with a catalogue message (the pre-round-3 `ERROR_MESSAGES` keys). */
export const ERROR_CODES = [
  "upload_session_not_open",
  "upload_incomplete",
  "dataset_version_conflict",
  "upload_expired",
  "size_mismatch",
  "sha256_mismatch",
  "content_type_mismatch",
  "part_size_mismatch",
  "unsafe_archive",
  "unexpected_part",
  "unsupported_content_type",
  "source_revoked",
  "verification_mismatch",
  "storage_unavailable",
  "storage_not_configured",
  "invalid_body",
  "invalid_part_numbers",
  "not_found",
  "purpose_forbidden",
  "purpose_not_granted",
  "not_an_upload_source",
  "unsupported_media_type",
] as const satisfies readonly Key0501[];
export type ErrorCode = (typeof ERROR_CODES)[number];

export function isKnownErrorCode(code: string): code is ErrorCode {
  return (ERROR_CODES as readonly string[]).includes(code);
}

/** Closed-vocabulary words. English is the raw value (the pre-round-3 option text); an unknown value is shown as received. */
const PURPOSE_AR: Record<string, string> = {
  training: "التدريب",
  validation: "التحقق",
  held_out_evaluation: "التقييم المحجوز",
  candidate_development: "تطوير المرشحين",
  baseline_only: "خط الأساس فقط",
};

export function purposeWord(value: string, locale: Locale): string {
  if (resolveLocale(locale) === "en") return value;
  return Object.hasOwn(PURPOSE_AR, value) ? PURPOSE_AR[value]! : value;
}

/** Closed vocabularies shared by the dataset-factory screens (FTF-0500A shows them too). English is the raw wire value. */
const SOURCE_TYPE_AR: Record<string, string> = {
  upload: "رفع ملف",
  s3: "S3",
  minio: "MinIO",
  database: "قاعدة بيانات",
  agent_traces: "آثار الوكلاء",
  production_conversations: "محادثات الإنتاج",
  api: "واجهة برمجية",
};
const CLASSIFICATION_AR: Record<string, string> = {
  public: "عامة",
  internal: "داخلية",
  confidential: "سرية",
  restricted: "مقيّدة",
};

export function sourceTypeWord(value: string, locale: Locale): string {
  if (resolveLocale(locale) === "en") return value;
  return Object.hasOwn(SOURCE_TYPE_AR, value) ? SOURCE_TYPE_AR[value]! : value;
}

export function classificationWord(value: string, locale: Locale): string {
  if (resolveLocale(locale) === "en") return value;
  return Object.hasOwn(CLASSIFICATION_AR, value) ? CLASSIFICATION_AR[value]! : value;
}
