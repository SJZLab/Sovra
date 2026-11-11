/**
 * Import-model wizard UI (FTF-H010-P5, Section 0.12/6.17/58A/68.1/72/77.6/81).
 *
 * A guided three-step flow on the CERTIFIED FTF-H010-P1/P2/P3 routes:
 *   1. choose an origin (endpoint model / full weights / adapter + base),
 *   2. fill the origin's own form,
 *   3. review and confirm, then `POST .../model-imports`;
 * plus the persisted job list (`GET .../model-imports`) with bounded-backoff polling.
 *
 * Honest scope (Law 11/13/17): validation is done by a worker that does not exist in this
 * repository yet (FTF-0801/H016), so a new job honestly reads "Queued - validation has not
 * started": no progress bar, percentage or ETA, and nothing is shown as succeeded until the
 * server says `succeeded` with a result model version. `blocked_external` is never shown as failed
 * or succeeded. The screen is UNWIRED until FTF-H029 mounts it.
 *
 * Structural negatives:
 *  - the endpoint branch has no artifact, digest or hash field and never renders a connection's
 *    `base_url` (it is never even read from the connection list);
 *  - the adapter base list holds only `full_weight` (imported_weights) versions, chosen by exact
 *    id: no family or name matching;
 *  - the POST has no idempotency key, so a lost response is NEVER blindly retried: the wizard
 *    re-lists the jobs and shows the job if one was created; resubmitting is a separate, explicit
 *    user action offered only when none matches.
 */

import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from "react";

import {
  AlertMessage,
  Button,
  EmptyState,
  ErrorSummary,
  Field,
  StatusBadge,
  kindForStatus,
  networkError,
  uiError,
  useSingleFlight,
  type ErrorSummaryItem,
  type UiError,
} from "../ui";
import { dirFor, resolveLocale, type Locale } from "../ui/phrases";

export const IMPORT_ORIGINS = [
  "external_endpoint",
  "imported_weights",
  "imported_adapter",
] as const;
export type ImportOrigin = (typeof IMPORT_ORIGINS)[number];
export const IMPORT_JOB_STATES = [
  "queued",
  "validating",
  "failed",
  "succeeded",
  "blocked_external",
] as const;
export type ImportJobState = (typeof IMPORT_JOB_STATES)[number];
const TERMINAL: ReadonlySet<string> = new Set(["failed", "succeeded", "blocked_external"]);

/** `model_import_schema.EXTERNAL_PROVIDER_MODEL_REF_MAX_LENGTH`. */
export const MAX_REF_LENGTH = 2048;
export const POLL_START_MS = 2000;
export const POLL_MAX_MS = 30000;
/** `model_version_routes.MODEL_VERSION_WRITE_ROLES` and `connection_routes.CONNECTION_ADMIN_ROLES`. */
export const WRITE_ROLES: readonly string[] = ["ml_engineer", "organization_admin"];
export const ADMIN_ROLES: readonly string[] = ["organization_admin"];

type Msg = { en: string; ar: string };

// ---------------------------------------------------------------------------------------------
// Messages. Every failure code the P2/P3 validators and the P1 routes can emit has an en AND an ar
// entry (a Python presence test reads these tables from this file's source).
// ---------------------------------------------------------------------------------------------
export const FAILURE_MESSAGES: Record<string, Msg> = {
  connection_revoked: { en: "The gateway connection was revoked.", ar: "تم إلغاء اتصال البوابة." },
  gateway_unavailable: { en: "The gateway could not be reached.", ar: "تعذر الوصول إلى البوابة." },
  gateway_access_denied: { en: "The gateway denied access.", ar: "رفضت البوابة الوصول." },
  alias_not_authorized: {
    en: "The model alias is not authorized on this gateway.",
    ar: "اسم النموذج غير مصرح به على هذه البوابة.",
  },
  route_attestation_unavailable: {
    en: "The route identity could not be attested.",
    ar: "تعذر إثبات هوية المسار.",
  },
  route_identity_unverified: {
    en: "The model that answered did not match the requested one.",
    ar: "النموذج الذي أجاب لا يطابق النموذج المطلوب.",
  },
  artifact_not_ready: { en: "The artifact is not ready.", ar: "القطعة غير جاهزة." },
  license_not_accepted: {
    en: "The artifact license has not been accepted.",
    ar: "لم يتم قبول ترخيص القطعة.",
  },
  base_license_not_accepted: {
    en: "The base model license has not been accepted.",
    ar: "لم يتم قبول ترخيص النموذج الأساسي.",
  },
  digest_mismatch: {
    en: "The files do not match the recorded digest.",
    ar: "الملفات لا تطابق البصمة المسجلة.",
  },
  artifact_source_unavailable: {
    en: "The artifact source is unavailable.",
    ar: "مصدر القطعة غير متاح.",
  },
  artifact_source_invalid: { en: "The artifact source is invalid.", ar: "مصدر القطعة غير صالح." },
  metadata_files_unavailable: {
    en: "The model metadata files cannot be read from this source.",
    ar: "لا يمكن قراءة ملفات بيانات النموذج من هذا المصدر.",
  },
  artifact_changed_during_validation: {
    en: "The artifact changed during validation.",
    ar: "تغيرت القطعة أثناء التحقق.",
  },
  base_not_validated: {
    en: "The base model version has not been validated.",
    ar: "إصدار النموذج الأساسي لم يتم التحقق منه.",
  },
  required_file_missing: { en: "A required file is missing.", ar: "ملف مطلوب مفقود." },
  adapter_base_mismatch: {
    en: "The adapter does not match the chosen base model.",
    ar: "المحوّل لا يطابق النموذج الأساسي المختار.",
  },
  unexpected_file: {
    en: "The artifact contains a file that is not allowed.",
    ar: "تحتوي القطعة على ملف غير مسموح به.",
  },
  unsafe_weight_format: {
    en: "The weights use a format that is not safe to load.",
    ar: "تستخدم الأوزان صيغة غير آمنة للتحميل.",
  },
  config_invalid: {
    en: "The model configuration file is not valid.",
    ar: "ملف إعدادات النموذج غير صالح.",
  },
  tokenizer_config_invalid: {
    en: "The tokenizer configuration file is not valid.",
    ar: "ملف إعدادات أداة الترميز غير صالح.",
  },
  adapter_config_invalid: {
    en: "The adapter configuration file is not valid.",
    ar: "ملف إعدادات المحوّل غير صالح.",
  },
  quantization_unverified: {
    en: "The quantization of the weights could not be verified.",
    ar: "تعذر التحقق من تكميم الأوزان.",
  },
  weights_header_invalid: {
    en: "The weights file header is not valid.",
    ar: "ترويسة ملف الأوزان غير صالحة.",
  },
  validator_input_invalid: {
    en: "The validation input was not valid.",
    ar: "مدخلات التحقق غير صالحة.",
  },
  adapter_base_incompatible: {
    en: "The adapter does not match the chosen base model.",
    ar: "المحوّل لا يطابق النموذج الأساسي المختار.",
  },
  tokenizer_mismatch: {
    en: "The tokenizer does not match the base model.",
    ar: "أداة الترميز لا تطابق النموذج الأساسي.",
  },
  // P1 route error codes (`model_import_routes.py`).
  invalid_body: { en: "The request was not valid.", ar: "الطلب غير صالح." },
  not_found: { en: "Not found.", ar: "غير موجود." },
};

const M = {
  title: { en: "Import a model", ar: "استيراد نموذج" },
  step1: { en: "Step 1 of 3: choose what to import", ar: "الخطوة 1 من 3: اختر ما تريد استيراده" },
  step2: { en: "Step 2 of 3: details", ar: "الخطوة 2 من 3: التفاصيل" },
  step3: { en: "Step 3 of 3: review and confirm", ar: "الخطوة 3 من 3: المراجعة والتأكيد" },
  originLegend: { en: "What do you want to import?", ar: "ماذا تريد أن تستورد؟" },
  originEndpoint: { en: "Endpoint model", ar: "نموذج عبر نقطة اتصال" },
  originEndpointHelp: {
    en: "An existing fine-tuned model served by a gateway. No weights are imported.",
    ar: "نموذج مضبوط موجود تقدمه بوابة. لا يتم استيراد أوزان.",
  },
  originWeights: { en: "Full weights", ar: "أوزان كاملة" },
  originWeightsHelp: {
    en: "A registered model artifact with its weights.",
    ar: "قطعة نموذج مسجلة مع أوزانها.",
  },
  originAdapter: { en: "Adapter and base", ar: "محوّل ونموذج أساسي" },
  originAdapterHelp: {
    en: "An adapter artifact on an exact, already validated base model version.",
    ar: "قطعة محوّل على إصدار نموذج أساسي محدد تم التحقق منه.",
  },
  next: { en: "Continue", ar: "متابعة" },
  back: { en: "Back", ar: "رجوع" },
  review: { en: "Review import", ar: "مراجعة الاستيراد" },
  startImport: { en: "Start import", ar: "بدء الاستيراد" },
  startImportNeedsConfirm: {
    en: "Tick the confirmation to start the import.",
    ar: "ضع علامة التأكيد لبدء الاستيراد.",
  },
  confirmLabel: {
    en: "I confirm these details are correct.",
    ar: "أؤكد أن هذه التفاصيل صحيحة.",
  },
  viewDenied: {
    en: "Your role can view imports but cannot start one.",
    ar: "يمكن لدورك عرض عمليات الاستيراد لكن لا يمكنه بدء واحدة.",
  },
  adminRequired: {
    en: "Requires an organization administrator",
    ar: "يتطلب مسؤول المؤسسة",
  },
  adminRequiredWhy: {
    en: "Endpoint imports use an organization gateway connection. Ask an organization administrator, or import weights or an adapter instead.",
    ar: "يستخدم استيراد نقطة الاتصال اتصال بوابة المؤسسة. اطلب من مسؤول المؤسسة، أو استورد أوزانًا أو محوّلًا بدلًا من ذلك.",
  },
  connection: { en: "Gateway connection", ar: "اتصال البوابة" },
  chooseConnection: { en: "Choose a connection", ar: "اختر اتصالًا" },
  noConnections: { en: "No active gateway connections", ar: "لا توجد اتصالات بوابة نشطة" },
  noConnectionsWhy: {
    en: "An administrator must add and validate a gateway connection first.",
    ar: "يجب أن يضيف المسؤول اتصال بوابة ويتحقق منه أولًا.",
  },
  catalogChoice: { en: "Model from the connection catalog", ar: "نموذج من كتالوج الاتصال" },
  catalogNone: { en: "Type the exact model reference", ar: "اكتب مرجع النموذج بالضبط" },
  refLabel: { en: "Model reference", ar: "مرجع النموذج" },
  refHelp: {
    en: "Sent exactly as typed (up to 2048 characters). Not trimmed or changed.",
    ar: "يُرسل كما كُتب بالضبط (حتى 2048 حرفًا). لا يُقتطع ولا يتغير.",
  },
  artifact: { en: "Model artifact", ar: "قطعة النموذج" },
  adapterArtifact: { en: "Adapter artifact", ar: "قطعة المحوّل" },
  baseVersion: { en: "Base model version", ar: "إصدار النموذج الأساسي" },
  chooseArtifact: { en: "Choose an artifact", ar: "اختر قطعة" },
  chooseBase: { en: "Choose a base version", ar: "اختر إصدارًا أساسيًا" },
  noArtifacts: { en: "No artifacts registered", ar: "لا توجد قطع مسجلة" },
  noArtifactsWhy: {
    en: "Register a model artifact first, then come back to import it.",
    ar: "سجل قطعة نموذج أولًا ثم عد لاستيرادها.",
  },
  noBases: { en: "No validated base versions", ar: "لا توجد إصدارات أساسية تم التحقق منها" },
  noBasesWhy: {
    en: "Import and validate a full-weights model first; it can then serve as a base.",
    ar: "استورد نموذج أوزان كاملة وتحقق منه أولًا؛ يمكنه حينها أن يكون أساسًا.",
  },
  statusLabel: { en: "Status", ar: "الحالة" },
  licenseLabel: { en: "License", ar: "الترخيص" },
  digestLabel: { en: "Digest", ar: "البصمة" },
  sourceLabel: { en: "Source", ar: "المصدر" },
  versionLabel: { en: "Version", ar: "الإصدار" },
  blockedNotReady: {
    en: "Not ready: it cannot be validated yet.",
    ar: "غير جاهزة: لا يمكن التحقق منها بعد.",
  },
  blockedLicense: { en: "License not accepted.", ar: "لم يتم قبول الترخيص." },
  licenseAccepted: { en: "accepted", ar: "مقبول" },
  licenseNotAccepted: { en: "not accepted", ar: "غير مقبول" },
  needChoice: { en: "Choose an option.", ar: "اختر خيارًا." },
  needRef: { en: "Enter the model reference.", ar: "أدخل مرجع النموذج." },
  refTooLong: {
    en: "The reference is longer than 2048 characters.",
    ar: "المرجع أطول من 2048 حرفًا.",
  },
  refControlChars: {
    en: "The reference contains characters that are not allowed.",
    ar: "يحتوي المرجع على أحرف غير مسموح بها.",
  },
  reviewSummary: { en: "Import summary", ar: "ملخص الاستيراد" },
  securityProfile: { en: "Project security profile", ar: "ملف أمان المشروع" },
  securityProfileUnavailable: { en: "Unavailable", ar: "غير متاح" },
  lineage: { en: "Expected lineage", ar: "النسب المتوقع" },
  lineageUnknown: { en: "Unknown", ar: "غير معروف" },
  lineagePartial: { en: "Partial", ar: "جزئي" },
  originLabel: { en: "Import type", ar: "نوع الاستيراد" },
  jobsHeading: { en: "Import jobs", ar: "مهام الاستيراد" },
  jobsEmpty: { en: "No imports yet", ar: "لا توجد عمليات استيراد بعد" },
  jobsEmptyWhy: {
    en: "Start an import above; it appears here and stays after you reload.",
    ar: "ابدأ استيرادًا أعلاه؛ سيظهر هنا ويبقى بعد إعادة تحميل الصفحة.",
  },
  loading: { en: "Loading…", ar: "جارٍ التحميل…" },
  submitting: { en: "Starting the import…", ar: "جارٍ بدء الاستيراد…" },
  refresh: { en: "Reload jobs", ar: "إعادة تحميل المهام" },
  queued: {
    en: "Queued – validation has not started",
    ar: "في الانتظار – لم يبدأ التحقق",
  },
  validating: { en: "Validating", ar: "قيد التحقق" },
  failedLabel: { en: "Validation failed", ar: "فشل التحقق" },
  blockedLabel: { en: "Blocked by an external dependency", ar: "محظور بسبب جهة خارجية" },
  succeededLabel: { en: "Validated", ar: "تم التحقق" },
  codeLabel: { en: "Code", ar: "الرمز" },
  openVersion: { en: "View model version", ar: "عرض إصدار النموذج" },
  jobCreated: {
    en: "Import started. It is queued and validation has not started.",
    ar: "بدأ الاستيراد. هو في الانتظار ولم يبدأ التحقق.",
  },
  jobFound: {
    en: "The import was created. It is shown below.",
    ar: "تم إنشاء الاستيراد. وهو معروض أدناه.",
  },
  noneFound: {
    en: "No import was created. You can start it again.",
    ar: "لم يتم إنشاء أي استيراد. يمكنك بدؤه مرة أخرى.",
  },
  verifying: {
    en: "Checking whether the import was created…",
    ar: "جارٍ التحقق من إنشاء الاستيراد…",
  },
  checkAgain: { en: "Check again", ar: "تحقق مرة أخرى" },
  polled: { en: "Import status updated.", ar: "تم تحديث حالة الاستيراد." },
  malformed: {
    en: "The server's answer could not be read. Nothing was changed.",
    ar: "تعذرت قراءة رد الخادم. لم يتغير شيء.",
  },
  failedLoadJobs: { en: "Could not load the import jobs.", ar: "تعذر تحميل مهام الاستيراد." },
  failedLoadOptions: { en: "Could not load the options.", ar: "تعذر تحميل الخيارات." },
  failedSubmit: { en: "The import could not be started.", ar: "تعذر بدء الاستيراد." },
  unknownOutcome: {
    en: "We could not confirm whether the import was created.",
    ar: "تعذر التأكد مما إذا كان الاستيراد قد أُنشئ.",
  },
  forbidden: { en: "You do not have permission for this.", ar: "ليست لديك صلاحية لذلك." },
  signIn: { en: "Your session ended.", ar: "انتهت جلستك." },
  unknownState: { en: "Unknown state", ar: "حالة غير معروفة" },
  blockedGeneric: {
    en: "Blocked by an external dependency",
    ar: "محظور بسبب جهة خارجية",
  },
  jobsNotLoaded: {
    en: "The import list has not loaded. Reload the jobs before starting an import.",
    ar: "لم تُحمَّل قائمة الاستيراد. أعد تحميل المهام قبل بدء الاستيراد.",
  },
  failedLoadProfile: {
    en: "Could not load the project security profile.",
    ar: "تعذر تحميل ملف أمان المشروع.",
  },
} satisfies Record<string, Msg>;
type MsgKey = keyof typeof M;

function tr(key: MsgKey, locale: Locale): string {
  return locale === "ar" ? M[key].ar : M[key].en;
}

/** Localized text for a server failure code; an unknown code renders the generic phrase + code. */
export function failureText(code: string | null, locale: Locale): string {
  if (code !== null && Object.hasOwn(FAILURE_MESSAGES, code)) {
    const m = FAILURE_MESSAGES[code]!;
    return locale === "ar" ? m.ar : m.en;
  }
  return tr("failedLabel", locale);
}

// ---------------------------------------------------------------------------------------------
// URLs
// ---------------------------------------------------------------------------------------------
const trimBase = (b: string) => b.replace(/\/$/, "");
const orgBase = (a: string, o: string) => `${trimBase(a)}/api/v1/organizations/${o}`;
export const modelImportsUrl = (a: string, o: string, p: string) =>
  `${orgBase(a, o)}/projects/${p}/model-imports`;
export const connectionsUrl = (a: string, o: string) => `${orgBase(a, o)}/connections/litellm`;
export const catalogUrl = (a: string, o: string, c: string) =>
  `${connectionsUrl(a, o)}/${c}/catalog`;
export const artifactsUrl = (a: string, o: string) => `${orgBase(a, o)}/artifacts`;
export const modelVersionsUrl = (a: string, o: string, p: string) =>
  `${orgBase(a, o)}/projects/${p}/model-versions`;
export const projectUrl = (a: string, o: string, p: string) => `${orgBase(a, o)}/projects/${p}`;

// ---------------------------------------------------------------------------------------------
// Strict parsing (UI CHECKLIST a): exact types; anything else is "malformed", never a guess.
// ---------------------------------------------------------------------------------------------
type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const strOrNull = (v: unknown): v is string | null => v === null || typeof v === "string";

export interface ImportJob {
  id: string;
  organization_id: string;
  project_id: string;
  requested_origin: ImportOrigin;
  parent_model_version_id: string | null;
  trainable_artifact_id: string | null;
  external_provider_model_ref: string | null;
  litellm_connection_id: string | null;
  result_model_version_id: string | null;
  state: ImportJobState;
  failure_code: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export function parseJob(v: unknown): ImportJob | null {
  if (!isRec(v)) return null;
  if (!isStr(v.id) || !isStr(v.organization_id) || !isStr(v.project_id)) return null;
  if (
    !isStr(v.requested_origin) ||
    !(IMPORT_ORIGINS as readonly string[]).includes(v.requested_origin)
  )
    return null;
  if (!isStr(v.state) || !(IMPORT_JOB_STATES as readonly string[]).includes(v.state)) return null;
  if (
    !strOrNull(v.parent_model_version_id) ||
    !strOrNull(v.trainable_artifact_id) ||
    !strOrNull(v.external_provider_model_ref) ||
    !strOrNull(v.litellm_connection_id) ||
    !strOrNull(v.result_model_version_id) ||
    !strOrNull(v.failure_code)
  )
    return null;
  if (!isStr(v.created_by) || !isStr(v.created_at) || !isStr(v.updated_at)) return null;
  // A success without a result version is not a success (Law 17).
  if (v.state === "succeeded" && v.result_model_version_id === null) return null;
  return v as unknown as ImportJob;
}

function parseJobList(v: unknown, org: string, project: string): ImportJob[] | null {
  if (!Array.isArray(v)) return null;
  const out: ImportJob[] = [];
  for (const item of v) {
    const job = parseJob(item);
    if (job === null || job.organization_id !== org || job.project_id !== project) return null;
    out.push(job);
  }
  return out;
}

interface ConnectionOpt {
  id: string;
  name: string;
}
function parseConnections(v: unknown): ConnectionOpt[] | null {
  if (!Array.isArray(v)) return null;
  const out: ConnectionOpt[] = [];
  for (const c of v) {
    // `base_url` is deliberately never read.
    if (!isRec(c) || !isStr(c.id) || !isStr(c.name) || !isStr(c.status)) return null;
    if (c.status === "active") out.push({ id: c.id, name: c.name });
  }
  return out;
}

/** Signature of the job states, so "status updated" is announced only when something changed. */
function jobSig(list: ImportJob[]): string {
  return list.map((j) => `${j.id}:${j.state}:${j.failure_code ?? ""}`).join("|");
}

function parseCatalog(v: unknown): string[] | null {
  if (!isRec(v) || !Array.isArray(v.entries)) return null;
  const out: string[] = [];
  for (const e of v.entries) {
    if (!isRec(e) || !isStr(e.id) || !isStr(e.status)) return null;
    // stale / removed entries are not callable: never offered as a choice.
    if (e.status === "callable") out.push(e.id);
  }
  return out;
}

interface ArtifactOpt {
  id: string;
  name: string;
  status: string;
  licenseId: string | null;
  licenseAccepted: boolean;
  digest: string | null;
  sourceType: string;
}
function parseArtifacts(v: unknown): ArtifactOpt[] | null {
  if (!Array.isArray(v)) return null;
  const out: ArtifactOpt[] = [];
  for (const a of v) {
    if (
      !isRec(a) ||
      !isStr(a.id) ||
      !isStr(a.display_name) ||
      !isStr(a.status) ||
      !isStr(a.source_type) ||
      !strOrNull(a.license_id) ||
      !strOrNull(a.license_accepted_at) ||
      !strOrNull(a.weights_digest)
    )
      return null;
    out.push({
      id: a.id,
      name: a.display_name,
      status: a.status,
      licenseId: a.license_id,
      licenseAccepted: a.license_accepted_at !== null,
      digest: a.weights_digest,
      sourceType: a.source_type,
    });
  }
  return out;
}

interface BaseOpt {
  id: string;
  versionNumber: number;
  digest: string | null;
}
function parseBases(v: unknown): BaseOpt[] | null {
  if (!Array.isArray(v)) return null;
  const out: BaseOpt[] = [];
  for (const m of v) {
    if (
      !isRec(m) ||
      !isStr(m.id) ||
      typeof m.version_number !== "number" ||
      !isStr(m.import_kind) ||
      !strOrNull(m.weights_digest)
    )
      return null;
    // Exact base only: full-weights versions; no family or name matching.
    if (m.import_kind === "full_weight")
      out.push({ id: m.id, versionNumber: m.version_number, digest: m.weights_digest });
  }
  return out;
}

function parseProfile(v: unknown): string | null {
  return isRec(v) && isStr(v.security_profile) ? v.security_profile : null;
}

interface SentBody {
  requested_origin: ImportOrigin;
  parent_model_version_id: string | null;
  trainable_artifact_id: string | null;
  external_provider_model_ref: string | null;
  litellm_connection_id: string | null;
}

/** UI CHECKLIST b: every field sent is compared with the response; any difference is malformed. */
export function bindsTo(job: ImportJob, sent: SentBody, org: string, project: string): boolean {
  return (
    job.organization_id === org &&
    job.project_id === project &&
    job.requested_origin === sent.requested_origin &&
    job.parent_model_version_id === sent.parent_model_version_id &&
    job.trainable_artifact_id === sent.trainable_artifact_id &&
    job.external_provider_model_ref === sent.external_provider_model_ref &&
    job.litellm_connection_id === sent.litellm_connection_id
  );
}

function validRef(ref: string): "ok" | "empty" | "long" | "chars" {
  if (ref.length === 0 || /^\s*$/.test(ref)) return "empty";
  // The server counts code points and refuses only Cc (control) and Cs (lone surrogates); the
  // `u` flag makes an astral character one code point, not two refused surrogate halves.
  if ([...ref].length > MAX_REF_LENGTH) return "long";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f-\u009f\ud800-\udfff]/u.test(ref)) return "chars";
  return "ok";
}

type Loaded<T> =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "error"; error: UiError };
type Unknown = null | "verifying" | "none_found" | "check_failed";

export interface ImportModelWizardProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  /** The caller's role in this organization (from the session); gates which controls exist. */
  role: string;
  locale?: Locale;
  onModelVersion?: (modelVersionId: string) => void;
}

export function ImportModelWizard(props: ImportModelWizardProps) {
  // Any identity change fully remounts: a reply for the old identity cannot land on the new one
  // and drafts are keyed by organization and project (UI CHECKLIST c).
  return (
    <Inner
      key={`${props.apiBaseUrl}|${props.organizationId}|${props.projectId}|${props.role}`}
      {...props}
    />
  );
}

async function getJson(url: string): Promise<{ status: number; ok: boolean; body: unknown }> {
  const response = await fetch(url, { credentials: "include" });
  let body: unknown = undefined;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  return { status: response.status, ok: response.ok, body };
}

function Inner({
  apiBaseUrl,
  organizationId,
  projectId,
  role,
  locale = "en",
  onModelVersion,
}: ImportModelWizardProps) {
  const lang = resolveLocale(locale);
  const dir = dirFor(locale);
  const uid = useId();
  const canWrite = WRITE_ROLES.includes(role);
  const isAdmin = ADMIN_ROLES.includes(role);

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const [jobs, setJobs] = useState<ImportJob[] | null>(null);
  const [jobsError, setJobsError] = useState<UiError | null>(null);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [announce, setAnnounce] = useState("");

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [origin, setOrigin] = useState<ImportOrigin | "">("");
  const [connectionId, setConnectionId] = useState("");
  const [catalogChoice, setCatalogChoice] = useState("");
  const [ref, setRef] = useState("");
  const [artifactId, setArtifactId] = useState("");
  const [baseId, setBaseId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<ErrorSummaryItem[]>([]);
  const [submitCount, setSubmitCount] = useState(0);

  const [connections, setConnections] = useState<Loaded<ConnectionOpt[]>>({ state: "idle" });
  const [catalog, setCatalog] = useState<Loaded<string[]>>({ state: "idle" });
  const [artifacts, setArtifacts] = useState<Loaded<ArtifactOpt[]>>({ state: "idle" });
  const [bases, setBases] = useState<Loaded<BaseOpt[]>>({ state: "idle" });
  const [profile, setProfile] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<UiError | null>(null);
  const [pollTick, setPollTick] = useState(0);
  const noticeRef = useRef<HTMLParagraphElement | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<UiError | null>(null);
  const [outcome, setOutcome] = useState<Unknown>(null);
  const [notice, setNotice] = useState<MsgKey | null>(null);
  const flight = useSingleFlight();
  const idsBeforeSubmit = useRef<Set<string>>(new Set());
  const lastSent = useRef<SentBody | null>(null);
  const reportedSuccess = useRef<Set<string>>(new Set());

  const loadJobs = useCallback(async (): Promise<ImportJob[] | null> => {
    try {
      const r = await getJson(modelImportsUrl(apiBaseUrl, organizationId, projectId));
      if (!alive.current) return null;
      if (!r.ok) {
        setJobsError(uiError(tr("failedLoadJobs", locale), kindForStatus(r.status)));
        return null;
      }
      const list = parseJobList(r.body, organizationId, projectId);
      if (list === null) {
        setJobsError(uiError(tr("malformed", locale), "retry"));
        return null;
      }
      setJobsError(null);
      setJobs(list);
      return list;
    } catch (caught) {
      if (!alive.current) return null;
      setJobsError(networkError(caught, locale));
      return null;
    }
  }, [apiBaseUrl, organizationId, projectId, locale]);

  // Persisted state survives refresh: the list is read from the server on mount.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await loadJobs();
      if (!cancelled && alive.current) setJobsLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [loadJobs]);

  // Project security profile, read-only (best effort: unavailable is shown, not guessed).
  useEffect(() => {
    if (!canWrite) return;
    void (async () => {
      try {
        const r = await getJson(projectUrl(apiBaseUrl, organizationId, projectId));
        if (!alive.current) return;
        const parsed = r.ok ? parseProfile(r.body) : null;
        if (parsed === null) {
          setProfile(null);
          setProfileError(
            uiError(
              tr(r.ok ? "malformed" : "failedLoadProfile", locale),
              r.ok ? "retry" : kindForStatus(r.status),
            ),
          );
        } else {
          setProfile(parsed);
          setProfileError(null);
        }
      } catch (caught) {
        if (alive.current) setProfileError(networkError(caught, locale));
      }
    })();
  }, [apiBaseUrl, organizationId, projectId, canWrite, locale]);

  // Bounded backoff polling (2 s -> 30 s) that stops when no job is non-terminal.
  const delayRef = useRef(POLL_START_MS);
  const lastSig = useRef("");
  useEffect(() => {
    if (jobs !== null && lastSig.current === "") lastSig.current = jobSig(jobs);
  }, [jobs]);
  const anyActive = jobs !== null && jobs.some((j) => !TERMINAL.has(j.state));
  useEffect(() => {
    if (!anyActive) {
      delayRef.current = POLL_START_MS;
      return;
    }
    const timer = setTimeout(() => {
      delayRef.current = Math.min(delayRef.current * 2, POLL_MAX_MS);
      void loadJobs().then((list) => {
        if (!alive.current) return;
        if (list !== null && jobSig(list) !== lastSig.current) setAnnounce(tr("polled", locale));
        if (list !== null) lastSig.current = jobSig(list);
        // A failed poll (error shown by loadJobs) must still re-arm the backoff.
        setPollTick((n) => n + 1);
      });
    }, delayRef.current);
    return () => clearTimeout(timer);
  }, [anyActive, pollTick, loadJobs, locale]);

  // After a successful create / found-after-lost-reply the form unmounts: focus the notice so
  // focus never falls to <body>.
  useEffect(() => {
    if (notice === "jobCreated" || notice === "jobFound") noticeRef.current?.focus();
  }, [notice]);

  // Origin-specific option loads.
  async function loadInto<T>(
    set: (l: Loaded<T>) => void,
    url: string,
    parse: (v: unknown) => T | null,
  ) {
    set({ state: "loading" });
    try {
      const r = await getJson(url);
      if (!alive.current) return;
      if (!r.ok) {
        set({
          state: "error",
          error: uiError(tr("failedLoadOptions", locale), kindForStatus(r.status)),
        });
        return;
      }
      const data = parse(r.body);
      set(
        data === null
          ? { state: "error", error: uiError(tr("malformed", locale), "retry") }
          : { state: "ok", data },
      );
    } catch (caught) {
      if (alive.current) set({ state: "error", error: networkError(caught, locale) });
    }
  }

  function loadForOrigin(o: ImportOrigin) {
    if (o === "external_endpoint") {
      if (isAdmin)
        void loadInto(setConnections, connectionsUrl(apiBaseUrl, organizationId), parseConnections);
    } else {
      void loadInto(setArtifacts, artifactsUrl(apiBaseUrl, organizationId), parseArtifacts);
      if (o === "imported_adapter")
        void loadInto(
          setBases,
          modelVersionsUrl(apiBaseUrl, organizationId, projectId),
          parseBases,
        );
    }
  }

  function chooseConnection(id: string) {
    setConnectionId(id);
    setCatalogChoice("");
    setCatalog({ state: "idle" });
    if (id) void loadInto(setCatalog, catalogUrl(apiBaseUrl, organizationId, id), parseCatalog);
  }

  /** Only versions a SUCCEEDED imported_weights job produced are validated bases (exact, no name matching). */
  const validatedBaseIds = new Set(
    (jobs ?? [])
      .filter(
        (j) =>
          j.state === "succeeded" &&
          j.requested_origin === "imported_weights" &&
          j.result_model_version_id !== null,
      )
      .map((j) => j.result_model_version_id as string),
  );
  const errFor = (id: string): string | undefined =>
    fieldErrors.find((f) => f.fieldId === id)?.message;

  const effectiveRef = catalogChoice !== "" ? catalogChoice : ref;

  function validateStep2(): ErrorSummaryItem[] {
    const errors: ErrorSummaryItem[] = [];
    if (origin === "external_endpoint") {
      if (!connectionId)
        errors.push({ fieldId: `${uid}-connection`, message: tr("needChoice", locale) });
      const v = validRef(effectiveRef);
      if (v === "empty") errors.push({ fieldId: `${uid}-ref`, message: tr("needRef", locale) });
      if (v === "long") errors.push({ fieldId: `${uid}-ref`, message: tr("refTooLong", locale) });
      if (v === "chars")
        errors.push({ fieldId: `${uid}-ref`, message: tr("refControlChars", locale) });
    } else {
      if (!artifactId)
        errors.push({ fieldId: `${uid}-artifact`, message: tr("needChoice", locale) });
      if (origin === "imported_adapter" && !baseId)
        errors.push({ fieldId: `${uid}-base`, message: tr("needChoice", locale) });
    }
    return errors;
  }

  function goReview() {
    const errors = validateStep2();
    setFieldErrors(errors);
    setSubmitCount((n) => n + 1);
    if (errors.length === 0) {
      setConfirmed(false);
      setStep(3);
    }
  }

  function buildBody(): SentBody | null {
    if (origin === "") return null;
    if (origin === "external_endpoint")
      return {
        requested_origin: origin,
        parent_model_version_id: null,
        trainable_artifact_id: null,
        external_provider_model_ref: effectiveRef,
        litellm_connection_id: connectionId,
      };
    return {
      requested_origin: origin,
      parent_model_version_id: origin === "imported_adapter" ? baseId : null,
      trainable_artifact_id: artifactId,
      external_provider_model_ref: null,
      litellm_connection_id: null,
    };
  }

  function resetWizard() {
    setStep(1);
    setOrigin("");
    setConnectionId("");
    setCatalogChoice("");
    setRef("");
    setArtifactId("");
    setBaseId("");
    setConfirmed(false);
    setFieldErrors([]);
  }

  function focusAlert() {
    setTimeout(() => {
      document.querySelector<HTMLElement>(`[data-testid="import-submit-error"]`)?.focus();
    }, 0);
  }

  /** After a write whose outcome is unknown: re-list; never re-POST. */
  async function verifyCreated() {
    setOutcome("verifying");
    const list = await loadJobs();
    if (!alive.current) return;
    if (list === null) {
      setOutcome("check_failed");
      return;
    }
    const sent = lastSent.current;
    const match =
      sent === null
        ? undefined
        : list.find(
            (j) =>
              !idsBeforeSubmit.current.has(j.id) && bindsTo(j, sent, organizationId, projectId),
          );
    if (match) {
      setOutcome(null);
      setSubmitError(null);
      setNotice("jobFound");
      resetWizard();
    } else {
      setOutcome("none_found");
      setNotice("noneFound");
    }
  }

  async function submit() {
    const body = buildBody();
    // The baseline of known job ids must exist, or a lost reply could never be verified.
    if (body === null || !confirmed || jobs === null || !flight.begin()) return;
    setSubmitting(true);
    setSubmitError(null);
    setNotice(null);
    setOutcome(null);
    lastSent.current = body;
    try {
      // Fresh baseline taken immediately before the POST (a stale one could "find" another
      // user's identical job after a lost reply). If it cannot be read, nothing is sent.
      const fresh = await loadJobs();
      if (!alive.current) return;
      if (fresh === null) {
        setSubmitError(uiError(tr("failedLoadJobs", locale), "retry"));
        focusAlert();
        return;
      }
      idsBeforeSubmit.current = new Set(fresh.map((j) => j.id));
      let response: Response;
      try {
        response = await fetch(modelImportsUrl(apiBaseUrl, organizationId, projectId), {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (caught) {
        if (!alive.current) return;
        setSubmitError(networkError(caught, locale, { write: true }));
        focusAlert();
        await verifyCreated();
        return;
      }
      if (!alive.current) return;
      if (response.ok) {
        let parsed: ImportJob | null = null;
        try {
          parsed = parseJob(await response.json());
        } catch {
          parsed = null;
        }
        if (!alive.current) return;
        // Only a typed 2xx with a body that binds to what was sent counts as success.
        if (
          response.status === 201 &&
          parsed !== null &&
          parsed.state === "queued" &&
          bindsTo(parsed, body, organizationId, projectId)
        ) {
          const created = parsed;
          setJobs((cur) => [...(cur ?? []).filter((j) => j.id !== created.id), created]);
          setNotice("jobCreated");
          resetWizard();
          return;
        }
        setSubmitError(uiError(tr("unknownOutcome", locale), "verify-state"));
        focusAlert();
        await verifyCreated();
        return;
      }
      let detail: string | null = null;
      try {
        const j: unknown = await response.json();
        if (isRec(j) && isStr(j.detail)) detail = j.detail;
      } catch {
        detail = null;
      }
      const status = response.status;
      const message =
        detail !== null && Object.hasOwn(FAILURE_MESSAGES, detail)
          ? failureText(detail, locale)
          : status === 403
            ? tr("forbidden", locale)
            : status === 401
              ? tr("signIn", locale)
              : tr("failedSubmit", locale);
      setSubmitError(uiError(message, kindForStatus(status)));
      focusAlert();
      // A 5xx may have committed before failing: check, do not blindly retry.
      if (status >= 500) await verifyCreated();
    } finally {
      flight.end();
      if (alive.current) setSubmitting(false);
    }
  }

  const rootStyle = { overflowWrap: "anywhere" as const, minInlineSize: 0, maxInlineSize: "100%" };
  const selStyle = { minBlockSize: "var(--target-min)", maxInlineSize: "100%" };

  const originName = (o: ImportOrigin): string =>
    tr(
      o === "external_endpoint"
        ? "originEndpoint"
        : o === "imported_weights"
          ? "originWeights"
          : "originAdapter",
      locale,
    );

  // ----- step renderers -------------------------------------------------------------------
  function renderOptions<T>(l: Loaded<T>, retry: () => void, body: (d: T) => ReactElement) {
    if (l.state === "loading")
      return (
        <p role="status" aria-busy="true">
          {tr("loading", locale)}
        </p>
      );
    if (l.state === "error")
      return (
        <>
          <AlertMessage error={l.error} locale={locale} testId="import-options-error" />
          <Button onClick={retry}>{tr("refresh", locale)}</Button>
        </>
      );
    if (l.state === "ok") return body(l.data);
    return null;
  }

  function artifactBlocked(a: ArtifactOpt): string | null {
    if (a.status !== "ready") return tr("blockedNotReady", locale);
    if (!a.licenseAccepted) return tr("blockedLicense", locale);
    return null;
  }

  function artifactSelect(id: string, label: string) {
    return renderOptions(
      artifacts,
      () => loadForOrigin(origin as ImportOrigin),
      (list) => {
        if (list.length === 0)
          return (
            <EmptyState
              title={tr("noArtifacts", locale)}
              why={tr("noArtifactsWhy", locale)}
              action={null}
            />
          );
        const chosen = list.find((a) => a.id === artifactId);
        const blocked = chosen ? artifactBlocked(chosen) : null;
        return (
          <div>
            <label htmlFor={id} style={{ display: "block" }}>
              {label}
            </label>
            <select
              id={id}
              value={artifactId}
              onChange={(e) => setArtifactId(e.target.value)}
              aria-invalid={errFor(id) ? true : undefined}
              aria-describedby={errFor(id) ? `${id}-error` : undefined}
              style={selStyle}
            >
              <option value="">{tr("chooseArtifact", locale)}</option>
              {list.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
            {errFor(id) ? (
              <div id={`${id}-error`} data-testid="artifact-error">
                {errFor(id)}
              </div>
            ) : null}
            {chosen ? (
              <dl data-testid="artifact-facts" style={{ overflowWrap: "anywhere" }}>
                <dt>{tr("statusLabel", locale)}</dt>
                <dd>
                  <bdi>{chosen.status}</bdi>
                </dd>
                <dt>{tr("licenseLabel", locale)}</dt>
                <dd>
                  <bdi>{chosen.licenseId ?? "—"}</bdi>{" "}
                  {chosen.licenseAccepted
                    ? tr("licenseAccepted", locale)
                    : tr("licenseNotAccepted", locale)}
                </dd>
                <dt>{tr("sourceLabel", locale)}</dt>
                <dd>
                  <bdi>{chosen.sourceType}</bdi>
                </dd>
                <dt>{tr("digestLabel", locale)}</dt>
                <dd>
                  <bdi style={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>
                    {chosen.digest ?? "—"}
                  </bdi>
                </dd>
              </dl>
            ) : null}
            {blocked ? <p data-testid="artifact-blocked-reason">{blocked}</p> : null}
          </div>
        );
      },
    );
  }

  function renderStep2() {
    if (origin === "external_endpoint") {
      if (!isAdmin)
        return (
          <div data-testid="endpoint-admin-required">
            <EmptyState
              title={tr("adminRequired", locale)}
              why={tr("adminRequiredWhy", locale)}
              action={null}
            />
          </div>
        );
      return (
        <>
          {renderOptions(
            connections,
            () => loadForOrigin("external_endpoint"),
            (list) => {
              if (list.length === 0)
                return (
                  <EmptyState
                    title={tr("noConnections", locale)}
                    why={tr("noConnectionsWhy", locale)}
                    action={null}
                  />
                );
              return (
                <div>
                  <label htmlFor={`${uid}-connection`} style={{ display: "block" }}>
                    {tr("connection", locale)}
                  </label>
                  <select
                    id={`${uid}-connection`}
                    value={connectionId}
                    onChange={(e) => chooseConnection(e.target.value)}
                    aria-invalid={errFor(`${uid}-connection`) ? true : undefined}
                    aria-describedby={
                      errFor(`${uid}-connection`) ? `${uid}-connection-error` : undefined
                    }
                    style={selStyle}
                  >
                    <option value="">{tr("chooseConnection", locale)}</option>
                    {list.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                  {errFor(`${uid}-connection`) ? (
                    <div id={`${uid}-connection-error`} data-testid="connection-error">
                      {errFor(`${uid}-connection`)}
                    </div>
                  ) : null}
                </div>
              );
            },
          )}
          {connectionId !== "" && catalog.state === "loading" ? (
            <p role="status" aria-busy="true">
              {tr("loading", locale)}
            </p>
          ) : null}
          {connectionId !== "" && catalog.state === "error" ? (
            <>
              <AlertMessage error={catalog.error} locale={locale} testId="import-catalog-error" />
              <Button onClick={() => chooseConnection(connectionId)}>
                {tr("refresh", locale)}
              </Button>
            </>
          ) : null}
          {connectionId !== "" && catalog.state === "ok" && catalog.data.length > 0 ? (
            <div>
              <label htmlFor={`${uid}-catalog`} style={{ display: "block" }}>
                {tr("catalogChoice", locale)}
              </label>
              <select
                id={`${uid}-catalog`}
                value={catalogChoice}
                onChange={(e) => setCatalogChoice(e.target.value)}
                style={selStyle}
              >
                <option value="">{tr("catalogNone", locale)}</option>
                {catalog.data.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          {connections.state === "ok" && connections.data.length > 0 ? (
            <Field
              id={`${uid}-ref`}
              label={tr("refLabel", locale)}
              helper={tr("refHelp", locale)}
              error={errFor(`${uid}-ref`)}
              requirement="required"
              locale={locale}
              type="text"
              dir="ltr"
              value={catalogChoice !== "" ? catalogChoice : ref}
              disabled={catalogChoice !== ""}
              onChange={(e) => setRef(e.target.value)}
              style={{ inlineSize: "100%", boxSizing: "border-box" }}
            />
          ) : null}
        </>
      );
    }
    return (
      <>
        {artifactSelect(
          `${uid}-artifact`,
          tr(origin === "imported_adapter" ? "adapterArtifact" : "artifact", locale),
        )}
        {origin === "imported_adapter"
          ? renderOptions(
              bases,
              () => loadForOrigin("imported_adapter"),
              (all) => {
                if (jobs === null || jobsError !== null)
                  return (
                    <>
                      <AlertMessage
                        error={jobsError ?? uiError(tr("failedLoadJobs", locale), "retry")}
                        locale={locale}
                        testId="import-bases-error"
                      />
                      <Button onClick={() => void loadJobs()}>{tr("refresh", locale)}</Button>
                    </>
                  );
                const list = all.filter((b) => validatedBaseIds.has(b.id));
                if (list.length === 0)
                  return (
                    <EmptyState
                      title={tr("noBases", locale)}
                      why={tr("noBasesWhy", locale)}
                      action={null}
                    />
                  );
                return (
                  <div>
                    <label htmlFor={`${uid}-base`} style={{ display: "block" }}>
                      {tr("baseVersion", locale)}
                    </label>
                    <select
                      id={`${uid}-base`}
                      value={baseId}
                      onChange={(e) => setBaseId(e.target.value)}
                      aria-invalid={errFor(`${uid}-base`) ? true : undefined}
                      aria-describedby={errFor(`${uid}-base`) ? `${uid}-base-error` : undefined}
                      style={selStyle}
                    >
                      <option value="">{tr("chooseBase", locale)}</option>
                      {list.map((b) => (
                        <option key={b.id} value={b.id}>
                          {`${tr("versionLabel", locale)} ${b.versionNumber} · ${tr("originWeights", locale)}`}
                        </option>
                      ))}
                    </select>
                    {errFor(`${uid}-base`) ? (
                      <div id={`${uid}-base-error`} data-testid="base-error">
                        {errFor(`${uid}-base`)}
                      </div>
                    ) : null}
                    {baseId ? (
                      <p style={{ overflowWrap: "anywhere" }}>
                        {tr("digestLabel", locale)}:{" "}
                        <bdi style={{ fontFamily: "monospace" }}>
                          {list.find((b) => b.id === baseId)?.digest ?? "—"}
                        </bdi>
                      </p>
                    ) : null}
                  </div>
                );
              },
            )
          : null}
      </>
    );
  }

  function renderReview() {
    if (origin === "") return null;
    const chosenArtifact =
      artifacts.state === "ok" ? artifacts.data.find((a) => a.id === artifactId) : undefined;
    const chosenConn =
      connections.state === "ok" ? connections.data.find((c) => c.id === connectionId) : undefined;
    const chosenBase = bases.state === "ok" ? bases.data.find((b) => b.id === baseId) : undefined;
    return (
      <section aria-labelledby={`${uid}-review`}>
        <h3 id={`${uid}-review`}>{tr("reviewSummary", locale)}</h3>
        <dl data-testid="import-summary" style={{ overflowWrap: "anywhere" }}>
          <dt>{tr("originLabel", locale)}</dt>
          <dd>{originName(origin)}</dd>
          {origin === "external_endpoint" ? (
            <>
              <dt>{tr("connection", locale)}</dt>
              <dd>
                <bdi>{chosenConn?.name ?? connectionId}</bdi>
              </dd>
              <dt>{tr("refLabel", locale)}</dt>
              <dd>
                <bdi>{effectiveRef}</bdi>
              </dd>
            </>
          ) : (
            <>
              <dt>{tr(origin === "imported_adapter" ? "adapterArtifact" : "artifact", locale)}</dt>
              <dd>
                <bdi>{chosenArtifact?.name ?? artifactId}</bdi>
              </dd>
              {origin === "imported_adapter" ? (
                <>
                  <dt>{tr("baseVersion", locale)}</dt>
                  <dd>
                    {chosenBase
                      ? `${tr("versionLabel", locale)} ${chosenBase.versionNumber}`
                      : baseId}
                  </dd>
                </>
              ) : null}
            </>
          )}
          <dt>{tr("securityProfile", locale)}</dt>
          <dd data-testid="import-security-profile">
            <bdi>{profile ?? tr("securityProfileUnavailable", locale)}</bdi>
          </dd>
          <dt>{tr("lineage", locale)}</dt>
          <dd data-testid="import-expected-lineage">
            {tr(origin === "external_endpoint" ? "lineageUnknown" : "lineagePartial", locale)}
          </dd>
        </dl>
        <label style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
          <input
            type="checkbox"
            data-testid="import-confirm"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            style={{ inlineSize: "var(--target-min)", blockSize: "var(--target-min)" }}
          />
          {tr("confirmLabel", locale)}
        </label>
      </section>
    );
  }

  function jobBadge(job: ImportJob) {
    switch (job.state) {
      case "queued":
        return (
          <>
            <StatusBadge state="queued" locale={locale} />
            <span data-testid="job-state-text"> {tr("queued", locale)}</span>
          </>
        );
      case "validating":
        return (
          <>
            <StatusBadge state="running" locale={locale} />
            <span data-testid="job-state-text"> {tr("validating", locale)}</span>
          </>
        );
      case "failed":
        return (
          <>
            <StatusBadge state="failed" locale={locale} />
            <span data-testid="job-state-text">
              {" "}
              {failureText(job.failure_code, locale)}
              {job.failure_code !== null && !Object.hasOwn(FAILURE_MESSAGES, job.failure_code) ? (
                <>
                  {" "}
                  ({tr("codeLabel", locale)}: <bdi>{job.failure_code}</bdi>)
                </>
              ) : null}
            </span>
          </>
        );
      case "blocked_external":
        return (
          <>
            <StatusBadge
              state="blocked"
              detail={
                job.failure_code !== null && Object.hasOwn(FAILURE_MESSAGES, job.failure_code)
                  ? failureText(job.failure_code, locale)
                  : tr("blockedGeneric", locale)
              }
              locale={locale}
            />
            <span data-testid="job-state-text">
              {" "}
              {tr("blockedLabel", locale)}
              {job.failure_code !== null && !Object.hasOwn(FAILURE_MESSAGES, job.failure_code) ? (
                <>
                  {" "}
                  ({tr("codeLabel", locale)}: <bdi>{job.failure_code}</bdi>)
                </>
              ) : null}
            </span>
          </>
        );
      case "succeeded":
        return (
          <span data-testid="job-state-text">
            <span aria-hidden="true">✓</span> {tr("succeededLabel", locale)}
          </span>
        );
      default:
        return <span>{tr("unknownState", locale)}</span>;
    }
  }

  const headingId = `${uid}-title`;
  const submitDisabled =
    submitting ||
    outcome === "verifying" ||
    outcome === "check_failed" ||
    !confirmed ||
    jobs === null;

  return (
    <div lang={lang} dir={dir} style={rootStyle} data-testid="import-model-wizard">
      <h2 id={headingId}>{tr("title", locale)}</h2>
      {announce ? (
        <p role="status" aria-live="polite" data-testid="import-announce">
          {announce}
        </p>
      ) : null}
      {notice ? (
        <p role="status" data-testid="import-notice" tabIndex={-1} ref={noticeRef}>
          {tr(notice, locale)}
        </p>
      ) : null}

      {!canWrite ? (
        <div data-testid="import-permission-denied">
          <EmptyState
            title={tr("forbidden", locale)}
            why={tr("viewDenied", locale)}
            action={null}
          />
        </div>
      ) : (
        <section aria-labelledby={headingId}>
          <p>{tr(step === 1 ? "step1" : step === 2 ? "step2" : "step3", locale)}</p>
          {profileError ? (
            <AlertMessage error={profileError} locale={locale} testId="import-profile-error" />
          ) : null}
          <ErrorSummary errors={fieldErrors} locale={locale} submitCount={submitCount} />
          {step === 1 ? (
            <fieldset>
              <legend>{tr("originLegend", locale)}</legend>
              {IMPORT_ORIGINS.map((o) => (
                <label
                  key={o}
                  style={{
                    display: "block",
                    border: "2px solid var(--color-border)",
                    padding: "var(--space-3)",
                    marginBlockEnd: "var(--space-2)",
                  }}
                >
                  <input
                    type="radio"
                    name={`${uid}-origin`}
                    value={o}
                    checked={origin === o}
                    onChange={() => {
                      setOrigin(o);
                      loadForOrigin(o);
                    }}
                  />{" "}
                  <strong>{originName(o)}</strong>
                  <span style={{ display: "block" }}>
                    {tr(
                      o === "external_endpoint"
                        ? "originEndpointHelp"
                        : o === "imported_weights"
                          ? "originWeightsHelp"
                          : "originAdapterHelp",
                      locale,
                    )}
                  </span>
                </label>
              ))}
              <Button
                variant="primary"
                disabled={origin === ""}
                onClick={() => {
                  setFieldErrors([]);
                  setStep(2);
                }}
              >
                {tr("next", locale)}
              </Button>
            </fieldset>
          ) : null}
          {step === 2 ? (
            <div>
              {renderStep2()}
              <div>
                <Button
                  onClick={() => {
                    setFieldErrors([]);
                    setStep(1);
                  }}
                >
                  {tr("back", locale)}
                </Button>
                {!(origin === "external_endpoint" && !isAdmin) ? (
                  <Button variant="primary" onClick={goReview}>
                    {tr("review", locale)}
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}
          {step === 3 ? (
            <div>
              {renderReview()}
              {submitError ? (
                <AlertMessage
                  error={submitError}
                  locale={locale}
                  testId="import-submit-error"
                  focusable
                />
              ) : null}
              {outcome === "verifying" ? <p role="status">{tr("verifying", locale)}</p> : null}
              {outcome === "check_failed" ? (
                <Button onClick={() => void verifyCreated()}>{tr("checkAgain", locale)}</Button>
              ) : null}
              <div>
                <Button onClick={() => setStep(2)} disabled={submitting}>
                  {tr("back", locale)}
                </Button>
                <Button
                  variant="primary"
                  data-testid="import-submit"
                  disabled={submitDisabled}
                  disabledReason={
                    submitting
                      ? undefined
                      : jobs === null
                        ? tr("jobsNotLoaded", locale)
                        : !confirmed
                          ? tr("startImportNeedsConfirm", locale)
                          : undefined
                  }
                  onClick={() => void submit()}
                >
                  {submitting ? tr("submitting", locale) : tr("startImport", locale)}
                </Button>
              </div>
            </div>
          ) : null}
        </section>
      )}

      <section aria-labelledby={`${uid}-jobs`}>
        <h3 id={`${uid}-jobs`}>{tr("jobsHeading", locale)}</h3>
        {jobsLoading ? (
          <p role="status" aria-busy="true">
            {tr("loading", locale)}
          </p>
        ) : null}
        {jobsError ? (
          <>
            <AlertMessage error={jobsError} locale={locale} testId="import-list-error" />
            <Button onClick={() => void loadJobs()}>{tr("refresh", locale)}</Button>
          </>
        ) : null}
        {!jobsLoading && !jobsError && jobs !== null && jobs.length === 0 ? (
          <EmptyState
            title={tr("jobsEmpty", locale)}
            why={tr("jobsEmptyWhy", locale)}
            action={null}
          />
        ) : null}
        {jobs !== null && jobs.length > 0 ? (
          <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: "var(--space-3)" }}>
            {jobs.map((job) => (
              <li
                key={job.id}
                data-testid={`job-${job.id}`}
                data-state={job.state}
                style={{ overflowWrap: "anywhere" }}
              >
                <strong>{originName(job.requested_origin)}</strong>{" "}
                {job.external_provider_model_ref !== null ? (
                  <bdi>{job.external_provider_model_ref}</bdi>
                ) : null}
                <div>{jobBadge(job)}</div>
                {job.state === "succeeded" &&
                job.result_model_version_id !== null &&
                onModelVersion ? (
                  <Button
                    onClick={() => {
                      reportedSuccess.current.add(job.id);
                      onModelVersion(job.result_model_version_id as string);
                    }}
                  >
                    {tr("openVersion", locale)}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
