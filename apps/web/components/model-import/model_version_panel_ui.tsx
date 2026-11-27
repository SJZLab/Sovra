/**
 * Model version panel (FTF-H010-P6; Section 0.12 roles, 6.17, 17, 58A "Allowed next operations",
 * 68.1, 72).
 *
 * Three read sections and two role writes, all on CERTIFIED routes:
 *   - Lineage, from the FTF-1001 item `GET .../model-versions/{id}`: `origin` and
 *     `lineage_completeness` verbatim; an unknown parent and the training data read "Unknown" (68.1:
 *     "Display unknown parent/training data as unknown"); the artifact digest only when the server
 *     has one -- an endpoint version reads "No training artifact", never a placeholder digest; the
 *     observed deployment fingerprint only when present, isolated in `<bdi>`.
 *   - Roles, from FTF-H010-P4 `GET .../roles` (active and revoked): assign through a confirm dialog
 *     naming the role and the version, revoke through a confirm dialog.
 *   - Actions, from FTF-H010-P4 `GET .../actions`: a READ-ONLY table of action x "Allowed" / "Not
 *     available" + the localized reason. No effect route exists for any action yet (evaluate ->
 *     FTF-R004/0601, continue_tuning -> FTF-0701/0801, request_qualification -> H020, deploy_route
 *     -> FTF-10xx, retire -> the status lifecycle), so no button is rendered for any of them (Law
 *     11: no placeholder controls).
 *
 * Fail closed: an action reads "Allowed" only when the server says `allowed === true` with no reason
 * code; anything else -- false, a missing decision, a non-boolean, an unknown action name, an
 * unknown reason -- reads "Not available" with the code shown.
 *
 * Role gating is the SERVER's: every control is offered to every reader and a 403 from the server is
 * shown as the permission error it is. The UI never decides who may assign `current_production` or
 * `judge` (Section 45 is enforced in `model_role_routes._require_role_allowed`).
 *
 * Idempotency: one `idempotency_key` (`crypto.randomUUID()`) per CONFIRMED assign attempt. "Try
 * again" after a lost response re-sends that same attempt with the same key, so the server replays
 * the stored row (200) and no second row exists. A new confirm is a new attempt with a new key.
 * A revoke retry whose first request did land gets 409 `role_not_assigned`: the panel re-fetches
 * and shows the revoked state as the outcome, not as an error.
 */

import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from "react";

import {
  AlertMessage,
  Button,
  DataTable,
  Dialog,
  EmptyState,
  kindForStatus,
  networkError,
  uiError,
  useSingleFlight,
  type UiError,
} from "../ui";
import { dirFor, phrase, resolveLocale, type Locale } from "../ui/phrases";

/** `model_role_schema.ROLES`, in the server's order. */
export const MODEL_ROLES = [
  "current_production",
  "baseline",
  "candidate",
  "teacher",
  "judge",
] as const;
/** `workers/training/continuation.ACTIONS`, in the server's order. */
export const MODEL_ACTIONS = [
  "evaluate",
  "compare",
  "continue_tuning",
  "exact_resume",
  "request_qualification",
  "retain_incumbent",
  "deploy_route",
  "retire",
] as const;

type Msg = { en: string; ar: string };

// ---------------------------------------------------------------------------------------------
// Reason codes. Every `CODE_*` constant of `workers/training/continuation.py` and every error code
// of `model_role_routes.py` has an en AND an ar entry; `tests/tasks/test_ftf_h010_p6.py` reads this
// table from this file's source and fails on any code without both.
// ---------------------------------------------------------------------------------------------
export const REASON_MESSAGES: Record<string, Msg> = {
  // continuation.py
  import_not_validated: {
    en: "The import has not been validated.",
    ar: "لم يتم التحقق من الاستيراد.",
  },
  license_not_accepted: {
    en: "The artifact license is not accepted.",
    ar: "ترخيص القطعة غير مقبول.",
  },
  adapter_base_incompatible: {
    en: "The adapter no longer matches a validated base model.",
    ar: "المحوّل لم يعد يطابق نموذجًا أساسيًا تم التحقق منه.",
  },
  weights_artifact_unusable: {
    en: "The weights artifact is no longer ready or its digest changed.",
    ar: "قطعة الأوزان لم تعد جاهزة أو تغيرت بصمتها.",
  },
  endpoint_not_trainable: {
    en: "An endpoint model cannot be trained further.",
    ar: "لا يمكن مواصلة تدريب نموذج عبر نقطة اتصال.",
  },
  no_trusted_checkpoint: {
    en: "There is no trusted checkpoint to resume from.",
    ar: "لا توجد نقطة حفظ موثوقة للاستئناف منها.",
  },
  prerequisite_not_available: {
    en: "A required capability is not available yet.",
    ar: "إمكانية مطلوبة غير متاحة بعد.",
  },
  not_current_production: {
    en: "This version is not the current production model.",
    ar: "هذا الإصدار ليس نموذج الإنتاج الحالي.",
  },
  unknown_action: { en: "This action is not recognised.", ar: "هذا الإجراء غير معروف." },
  // model_role_routes.py
  invalid_body: { en: "The request was not valid.", ar: "الطلب غير صالح." },
  invalid_role: { en: "That role does not exist.", ar: "هذا الدور غير موجود." },
  forbidden: {
    en: "Your role in this organization cannot make this change.",
    ar: "دورك في هذه المؤسسة لا يسمح بهذا التغيير.",
  },
  not_found: {
    en: "This model version was not found.",
    ar: "لم يتم العثور على إصدار النموذج هذا.",
  },
  role_already_assigned: {
    en: "This version already holds that role.",
    ar: "هذا الإصدار يحمل هذا الدور بالفعل.",
  },
  incumbent_exists: {
    en: "Another version is already the current production model.",
    ar: "إصدار آخر هو نموذج الإنتاج الحالي بالفعل.",
  },
  self_judging_role_conflict: {
    en: "A version cannot judge itself: it is a candidate or baseline in this project.",
    ar: "لا يمكن لإصدار أن يحكم على نفسه: فهو مرشح أو خط أساس في هذا المشروع.",
  },
  role_not_assigned: {
    en: "This version does not hold that role.",
    ar: "هذا الإصدار لا يحمل هذا الدور.",
  },
  idempotency_key_reused: {
    en: "This attempt was already used for a different request.",
    ar: "تم استخدام هذه المحاولة بالفعل لطلب مختلف.",
  },
  model_facts_invalid: {
    en: "The stored facts for this version could not be checked.",
    ar: "تعذر التحقق من البيانات المخزنة لهذا الإصدار.",
  },
};

const M = {
  title: { en: "Model version", ar: "إصدار النموذج" },
  version: { en: "Version", ar: "الإصدار" },
  lineage: { en: "Lineage", ar: "السلالة" },
  origin: { en: "Origin", ar: "المصدر" },
  completeness: { en: "Lineage completeness", ar: "اكتمال السلالة" },
  parent: { en: "Parent model version", ar: "إصدار النموذج الأصل" },
  trainingData: { en: "Training data", ar: "بيانات التدريب" },
  digest: { en: "Artifact digest", ar: "بصمة القطعة" },
  fingerprint: { en: "Observed deployment fingerprint", ar: "بصمة النشر المرصودة" },
  providerRef: { en: "Provider model reference", ar: "مرجع نموذج المزوّد" },
  unknown: { en: "Unknown", ar: "غير معروف" },
  noArtifact: { en: "No training artifact", ar: "لا توجد قطعة تدريب" },
  originExternalEndpoint: { en: "Endpoint model", ar: "نموذج عبر نقطة اتصال" },
  originImportedWeights: { en: "Imported full weights", ar: "أوزان كاملة مستوردة" },
  originImportedAdapter: { en: "Imported adapter", ar: "محوّل مستورد" },
  originFactoryTrained: { en: "Trained in Sovra", ar: "مدرَّب في المصنع" },
  completeComplete: { en: "Complete", ar: "مكتملة" },
  completePartial: { en: "Partial", ar: "جزئية" },
  completeUnknown: { en: "Unknown", ar: "غير معروفة" },
  unrecognised: { en: "Unrecognised value", ar: "قيمة غير معروفة" },
  loadingLineage: { en: "Loading the model version…", ar: "جارٍ تحميل إصدار النموذج…" },
  loadingRoles: { en: "Loading roles…", ar: "جارٍ تحميل الأدوار…" },
  loadingActions: { en: "Loading actions…", ar: "جارٍ تحميل الإجراءات…" },
  failedLineage: {
    en: "Could not load the model version.",
    ar: "تعذر تحميل إصدار النموذج.",
  },
  failedRoles: { en: "Could not load the roles.", ar: "تعذر تحميل الأدوار." },
  failedActions: { en: "Could not load the actions.", ar: "تعذر تحميل الإجراءات." },
  malformed: {
    en: "The server answered with data that could not be read. Nothing was changed.",
    ar: "أجاب الخادم ببيانات تعذرت قراءتها. لم يتغير شيء.",
  },
  reload: { en: "Load again", ar: "إعادة التحميل" },
  roles: { en: "Roles", ar: "الأدوار" },
  rolesCaption: {
    en: "Role assignments, active and revoked",
    ar: "تعيينات الأدوار، النشطة والملغاة",
  },
  colRole: { en: "Role", ar: "الدور" },
  colState: { en: "State", ar: "الحالة" },
  colAssignedBy: { en: "Assigned by", ar: "عُيّن بواسطة" },
  colAssignedAt: { en: "Assigned at", ar: "تاريخ التعيين" },
  colRevokedAt: { en: "Revoked at", ar: "تاريخ الإلغاء" },
  colChange: { en: "Change", ar: "تغيير" },
  active: { en: "Active", ar: "نشط" },
  revoked: { en: "Revoked", ar: "ملغى" },
  noRolesTitle: { en: "No roles yet", ar: "لا توجد أدوار بعد" },
  noRolesWhy: {
    en: "A role records what this version is for in the project: production incumbent, baseline, candidate, teacher or judge.",
    ar: "يسجل الدور الغرض من هذا الإصدار في المشروع: نموذج الإنتاج الحالي أو خط الأساس أو مرشح أو معلّم أو حَكَم.",
  },
  noRolesAction: { en: "Assign a role below.", ar: "عيّن دورًا أدناه." },
  assignLegend: { en: "Assign a role", ar: "تعيين دور" },
  roleSelect: { en: "Role to assign", ar: "الدور المراد تعيينه" },
  roleHelp: {
    en: "Current production and judge need an organization admin; the server decides.",
    ar: "يتطلب الإنتاج الحالي والحَكَم مسؤولًا عن المؤسسة؛ والخادم هو من يقرر.",
  },
  assign: { en: "Assign role", ar: "تعيين الدور" },
  revoke: { en: "Revoke", ar: "إلغاء الدور" },
  assignTitle: { en: "Assign this role?", ar: "هل تريد تعيين هذا الدور؟" },
  assignBodyBefore: { en: "Assign the role", ar: "تعيين الدور" },
  assignBodyMiddle: { en: "to model version", ar: "لإصدار النموذج" },
  assignBodyAfter: {
    en: "It takes effect at once and is recorded in the audit log.",
    ar: "يسري فورًا ويُسجل في سجل التدقيق.",
  },
  revokeTitle: { en: "Revoke this role?", ar: "هل تريد إلغاء هذا الدور؟" },
  revokeBodyBefore: { en: "Revoke the role", ar: "إلغاء الدور" },
  revokeBodyMiddle: { en: "from model version", ar: "من إصدار النموذج" },
  revokeBodyAfter: {
    en: "The assignment is kept as revoked and cannot be restored; assign the role again if needed.",
    ar: "يبقى التعيين محفوظًا كملغى ولا يمكن استعادته؛ عيّن الدور مجددًا عند الحاجة.",
  },
  confirmAssign: { en: "Assign role", ar: "تعيين الدور" },
  confirmRevoke: { en: "Revoke role", ar: "إلغاء الدور" },
  keep: { en: "Keep as is", ar: "الإبقاء كما هو" },
  assigned: { en: "Role assigned:", ar: "تم تعيين الدور:" },
  revokedNotice: { en: "Role revoked:", ar: "تم إلغاء الدور:" },
  alreadyRevoked: {
    en: "This role is already revoked; the list shows its current state:",
    ar: "هذا الدور ملغى بالفعل؛ تعرض القائمة حالته الحالية:",
  },
  tryAgain: { en: "Try again", ar: "حاول مجددًا" },
  failedAssign: { en: "The role was not assigned.", ar: "لم يتم تعيين الدور." },
  failedRevoke: { en: "The role was not revoked.", ar: "لم يتم إلغاء الدور." },
  unboundOutcome: {
    en: "The server's answer does not confirm this change. The list below shows the current state; check it before trying again.",
    ar: "رد الخادم لا يؤكد هذا التغيير. تعرض القائمة أدناه الحالة الحالية؛ تحقق منها قبل المحاولة مجددًا.",
  },
  actions: { en: "Actions", ar: "الإجراءات" },
  actionsCaption: {
    en: "What may be done with this version, as decided by the server",
    ar: "ما يمكن فعله بهذا الإصدار، كما يقرره الخادم",
  },
  actionsNote: {
    en: "This table only reports the server's decision. None of these actions can be started from this screen yet.",
    ar: "يعرض هذا الجدول قرار الخادم فقط. لا يمكن بدء أي من هذه الإجراءات من هذه الشاشة بعد.",
  },
  colAction: { en: "Action", ar: "الإجراء" },
  colDecision: { en: "Decision", ar: "القرار" },
  colReason: { en: "Reason or plan", ar: "السبب أو الخطة" },
  allowed: { en: "Allowed", ar: "مسموح" },
  notAvailable: { en: "Not available", ar: "غير متاح" },
  reasonUnrecognised: { en: "Reason not recognised.", ar: "السبب غير معروف." },
  noDecision: { en: "The server returned no decision.", ar: "لم يُرجع الخادم أي قرار." },
  planNewRun: {
    en: "A new training run from step 0, with no optimizer state.",
    ar: "تشغيل تدريب جديد من الخطوة 0، بدون حالة المُحسِّن.",
  },
  planResumeBefore: {
    en: "Exact resume from checkpoint step",
    ar: "استئناف دقيق من خطوة نقطة الحفظ",
  },
  actEvaluate: { en: "Evaluate", ar: "تقييم" },
  actCompare: { en: "Compare", ar: "مقارنة" },
  actContinueTuning: { en: "Continue tuning", ar: "مواصلة الضبط" },
  actExactResume: { en: "Resume from checkpoint", ar: "الاستئناف من نقطة حفظ" },
  actRequestQualification: { en: "Request qualification", ar: "طلب التأهيل" },
  actRetainIncumbent: { en: "Retain incumbent", ar: "الإبقاء على النموذج الحالي" },
  actDeployRoute: { en: "Deploy or route", ar: "النشر أو التوجيه" },
  actRetire: { en: "Retire", ar: "إيقاف" },
  roleCurrentProduction: { en: "Current production", ar: "الإنتاج الحالي" },
  roleBaseline: { en: "Baseline", ar: "خط الأساس" },
  roleCandidate: { en: "Candidate", ar: "مرشح" },
  roleTeacher: { en: "Teacher", ar: "معلّم" },
  roleJudge: { en: "Judge", ar: "حَكَم" },
} satisfies Record<string, Msg>;
type MsgKey = keyof typeof M;

function tr(key: MsgKey, locale: Locale): string {
  return locale === "ar" ? M[key].ar : M[key].en;
}

const ROLE_KEY: Readonly<Record<string, MsgKey>> = {
  current_production: "roleCurrentProduction",
  baseline: "roleBaseline",
  candidate: "roleCandidate",
  teacher: "roleTeacher",
  judge: "roleJudge",
};
const ACTION_KEY: Readonly<Record<string, MsgKey>> = {
  evaluate: "actEvaluate",
  compare: "actCompare",
  continue_tuning: "actContinueTuning",
  exact_resume: "actExactResume",
  request_qualification: "actRequestQualification",
  retain_incumbent: "actRetainIncumbent",
  deploy_route: "actDeployRoute",
  retire: "actRetire",
};
const ORIGIN_KEY: Readonly<Record<string, MsgKey>> = {
  external_endpoint: "originExternalEndpoint",
  imported_weights: "originImportedWeights",
  imported_adapter: "originImportedAdapter",
  factory_trained: "originFactoryTrained",
};
const COMPLETENESS_KEY: Readonly<Record<string, MsgKey>> = {
  complete: "completeComplete",
  partial: "completePartial",
  unknown: "completeUnknown",
};

function labelOf(table: Readonly<Record<string, MsgKey>>, value: string, locale: Locale): string {
  return Object.hasOwn(table, value) ? tr(table[value]!, locale) : tr("unrecognised", locale);
}

/** Localized reason; an unknown code gets the "not recognised" sentence (the code is shown beside it). */
export function reasonText(code: string, locale: Locale): string {
  if (Object.hasOwn(REASON_MESSAGES, code)) {
    const m = REASON_MESSAGES[code]!;
    return locale === "ar" ? m.ar : m.en;
  }
  return tr("reasonUnrecognised", locale);
}

// ---------------------------------------------------------------------------------------------
// URLs
// ---------------------------------------------------------------------------------------------
const trimBase = (b: string) => b.replace(/\/$/, "");
export const modelVersionUrl = (a: string, o: string, p: string, v: string) =>
  `${trimBase(a)}/api/v1/organizations/${o}/projects/${p}/model-versions/${v}`;
export const rolesUrl = (a: string, o: string, p: string, v: string) =>
  `${modelVersionUrl(a, o, p, v)}/roles`;
export const revokeUrl = (a: string, o: string, p: string, v: string, role: string) =>
  `${rolesUrl(a, o, p, v)}/${encodeURIComponent(role)}/revoke`;
export const actionsUrl = (a: string, o: string, p: string, v: string) =>
  `${modelVersionUrl(a, o, p, v)}/actions`;

// ---------------------------------------------------------------------------------------------
// Strict parsing: exact types; anything else is "malformed", never a guess.
// ---------------------------------------------------------------------------------------------
type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const strOrNull = (v: unknown): v is string | null => v === null || typeof v === "string";

export interface VersionItem {
  id: string;
  version_number: number;
  origin: string;
  lineage_completeness: string;
  parent_model_version_id: string | null;
  weights_digest: string | null;
  external_provider_model_ref: string | null;
  observed_deployment_fingerprint: string | null;
}

export function parseVersion(v: unknown, id: string): VersionItem | null {
  if (!isRec(v) || v.id !== id || typeof v.version_number !== "number") return null;
  if (!isStr(v.origin) || !isStr(v.lineage_completeness)) return null;
  for (const k of [
    "parent_model_version_id",
    "weights_digest",
    "external_provider_model_ref",
    "observed_deployment_fingerprint",
  ]) {
    if (!strOrNull(v[k])) return null;
  }
  return {
    id: v.id,
    version_number: v.version_number,
    origin: v.origin,
    lineage_completeness: v.lineage_completeness,
    parent_model_version_id: v.parent_model_version_id as string | null,
    weights_digest: v.weights_digest as string | null,
    external_provider_model_ref: v.external_provider_model_ref as string | null,
    observed_deployment_fingerprint: v.observed_deployment_fingerprint as string | null,
  };
}

export interface RoleRow {
  id: string;
  role: string;
  assigned_by: string;
  assigned_at: string;
  revoked_at: string | null;
}

/**
 * A write counts as done only when the returned row is the one asked for (UI CHECKLIST b/d/e, Law
 * 17): same role and version; for an assign the same idempotency key and still active, for a revoke
 * now revoked. A non-JSON 200, a `{}` 200 or another row is NOT success.
 */
export function bindsTo(
  body: unknown,
  sent: { kind: "assign" | "revoke"; role: string; key?: string },
  versionId: string,
): boolean {
  const rows = parseRoles([body], versionId);
  if (!rows || rows.length !== 1 || !isRec(body)) return false;
  const row = rows[0]!;
  if (row.role !== sent.role) return false;
  if (sent.kind === "assign") return body.idempotency_key === sent.key && row.revoked_at === null;
  return row.revoked_at !== null;
}

export function parseRoles(v: unknown, versionId: string): RoleRow[] | null {
  if (!Array.isArray(v)) return null;
  const out: RoleRow[] = [];
  for (const r of v) {
    if (!isRec(r) || !isStr(r.id) || !isStr(r.role) || r.model_version_id !== versionId)
      return null;
    if (!isStr(r.assigned_by) || !isStr(r.assigned_at) || !strOrNull(r.revoked_at)) return null;
    out.push({
      id: r.id,
      role: r.role,
      assigned_by: r.assigned_by,
      assigned_at: r.assigned_at,
      revoked_at: r.revoked_at,
    });
  }
  return out;
}

export interface Decision {
  action: string;
  allowed: boolean;
  /** The reason code, or null when allowed. "missing" when the server gave no decision. */
  code: string | null;
  plan: { kind: "new_run" } | { kind: "exact_resume"; step: number } | null;
}

function parsePlan(p: unknown): Decision["plan"] {
  if (!isRec(p)) return null;
  if (p.kind === "new_run" && p.start_step === 0) return { kind: "new_run" };
  if (
    p.kind === "exact_resume" &&
    typeof p.start_step === "number" &&
    Number.isInteger(p.start_step) &&
    p.start_step >= 1
  )
    return { kind: "exact_resume", step: p.start_step };
  return null;
}

/**
 * One row per known action in the server's order, then any action name the server returned that
 * this UI does not know (shown, never allowed). Fail closed: "Allowed" only for `allowed === true`
 * with `code === null`.
 */
export function parseActions(v: unknown, versionId: string): Decision[] | null {
  if (!isRec(v) || v.model_version_id !== versionId || !isRec(v.actions)) return null;
  const actions = v.actions;
  const names = [
    ...MODEL_ACTIONS,
    ...Object.keys(actions).filter((k) => !(MODEL_ACTIONS as readonly string[]).includes(k)),
  ];
  return names.map((action) => {
    const known = (MODEL_ACTIONS as readonly string[]).includes(action);
    const d = Object.hasOwn(actions, action) ? actions[action] : undefined;
    if (!isRec(d)) return { action, allowed: false, code: null, plan: null };
    const code = isStr(d.code) ? d.code : null;
    const allowed = known && d.allowed === true && d.code === null;
    if (!known) return { action, allowed: false, code: code ?? "unknown_action", plan: null };
    const plan = allowed ? parsePlan(d.plan) : null;
    // continue_tuning and exact_resume are only meaningful with the server's plan: an "allowed"
    // without a valid one (e.g. a resume at step 0) is not shown as allowed (fail closed).
    const wanted =
      action === "continue_tuning" ? "new_run" : action === "exact_resume" ? "exact_resume" : null;
    if (allowed && wanted !== null && plan?.kind !== wanted)
      return { action, allowed: false, code: null, plan: null };
    return { action, allowed, code, plan };
  });
}

// ---------------------------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------------------------
export interface ModelVersionPanelProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  modelVersionId: string;
  locale?: Locale;
}

export function ModelVersionPanel(props: ModelVersionPanelProps): ReactElement {
  // An identity change fully remounts, so a reply for the old version can never land on the new one.
  return (
    <Inner
      key={`${props.apiBaseUrl}|${props.organizationId}|${props.projectId}|${props.modelVersionId}`}
      {...props}
    />
  );
}

type Loaded<T> =
  { state: "loading" } | { state: "ok"; value: T } | { state: "error"; error: UiError };

interface Reply {
  status: number;
  ok: boolean;
  body: unknown;
}

async function send(url: string, init: RequestInit = {}): Promise<Reply> {
  const response = await fetch(url, { credentials: "include", ...init });
  let body: unknown = undefined;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  return { status: response.status, ok: response.ok, body };
}

function detailOf(body: unknown): string | null {
  return isRec(body) && isStr(body.detail) ? body.detail : null;
}

/** The localized server error for a failed reply: `<lead> <reason>` with the code kept visible. */
function replyError(lead: string, reply: Reply, locale: Locale): UiError {
  const code = detailOf(reply.body);
  const message = code ? `${lead} ${reasonText(code, locale)} (${code})` : lead;
  return uiError(message, kindForStatus(reply.status));
}

type Attempt = { kind: "assign"; role: string; key: string } | { kind: "revoke"; role: string };
type Outcome =
  | { kind: "none" }
  | { kind: "pending" }
  | { kind: "notice"; text: MsgKey; role: string }
  | { kind: "error"; error: UiError; retry: boolean };

/** A fresh RFC 4122 v4 id from the platform CSPRNG. `crypto.randomUUID` exists only in a secure
 *  context, so a page served over plain HTTP builds the same v4 shape from `getRandomValues`. */
export function newKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function Inner({
  apiBaseUrl,
  organizationId,
  projectId,
  modelVersionId,
  locale = "en",
}: ModelVersionPanelProps) {
  const lang = resolveLocale(locale);
  const dir = dirFor(locale);
  const uid = useId();
  const args = [apiBaseUrl, organizationId, projectId, modelVersionId] as const;

  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const [version, setVersion] = useState<Loaded<VersionItem>>({ state: "loading" });
  const [roles, setRoles] = useState<Loaded<RoleRow[]>>({ state: "loading" });
  const [actions, setActions] = useState<Loaded<Decision[]>>({ state: "loading" });

  const loadVersion = useCallback(async () => {
    setVersion({ state: "loading" });
    try {
      const r = await send(modelVersionUrl(...args));
      if (!alive.current) return;
      if (!r.ok)
        return setVersion({
          state: "error",
          error: replyError(tr("failedLineage", locale), r, locale),
        });
      const parsed = parseVersion(r.body, modelVersionId);
      setVersion(
        parsed
          ? { state: "ok", value: parsed }
          : { state: "error", error: uiError(tr("malformed", locale), "retry") },
      );
    } catch (caught) {
      if (alive.current) setVersion({ state: "error", error: networkError(caught, locale) });
    }
  }, []);

  // Only the newest roles/actions request may write state (a refresh after a write can overlap).
  const rolesSeq = useRef(0);
  const loadRoles = useCallback(async (): Promise<RoleRow[] | null> => {
    const seq = ++rolesSeq.current;
    const latest = () => alive.current && seq === rolesSeq.current;
    try {
      const r = await send(rolesUrl(...args));
      if (!latest()) return null;
      if (!r.ok) {
        setRoles({ state: "error", error: replyError(tr("failedRoles", locale), r, locale) });
        return null;
      }
      const parsed = parseRoles(r.body, modelVersionId);
      setRoles(
        parsed
          ? { state: "ok", value: parsed }
          : { state: "error", error: uiError(tr("malformed", locale), "retry") },
      );
      return parsed;
    } catch (caught) {
      if (latest()) setRoles({ state: "error", error: networkError(caught, locale) });
      return null;
    }
  }, []);

  const actionsSeq = useRef(0);
  const loadActions = useCallback(async () => {
    const seq = ++actionsSeq.current;
    const latest = () => alive.current && seq === actionsSeq.current;
    try {
      const r = await send(actionsUrl(...args));
      if (!latest()) return;
      if (!r.ok)
        return setActions({
          state: "error",
          error: replyError(tr("failedActions", locale), r, locale),
        });
      const parsed = parseActions(r.body, modelVersionId);
      setActions(
        parsed
          ? { state: "ok", value: parsed }
          : { state: "error", error: uiError(tr("malformed", locale), "retry") },
      );
    } catch (caught) {
      if (latest()) setActions({ state: "error", error: networkError(caught, locale) });
    }
  }, []);

  useEffect(() => {
    void loadVersion();
    void loadRoles();
    void loadActions();
  }, [loadVersion, loadRoles, loadActions]);

  // ---- role writes ---------------------------------------------------------------------------
  const [choice, setChoice] = useState<string>(MODEL_ROLES[2]);
  const [dialog, setDialog] = useState<{ kind: "assign" | "revoke"; role: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "none" });
  const outcomeRef = useRef<HTMLDivElement | null>(null);
  const [restoreTo, setRestoreTo] = useState<HTMLElement | null>(null);
  const attempt = useRef<Attempt | null>(null);
  const flight = useSingleFlight();

  // The outcome region takes focus whenever a confirmed write settles (never <body>).
  useEffect(() => {
    if (outcome.kind === "notice" || outcome.kind === "error") outcomeRef.current?.focus();
  }, [outcome]);

  const run = useCallback(async () => {
    const a = attempt.current;
    if (!a || !flight.begin()) return;
    setOutcome({ kind: "pending" });
    try {
      let reply: Reply;
      try {
        reply =
          a.kind === "assign"
            ? await send(rolesUrl(...args), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ role: a.role, idempotency_key: a.key }),
              })
            : await send(revokeUrl(...args, a.role), { method: "POST" });
      } catch (caught) {
        if (!alive.current) return;
        // The outcome is unknown: keep the attempt (same key) for "Try again" and show the state.
        setOutcome({
          kind: "error",
          error: networkError(caught, locale, { write: true }),
          retry: true,
        });
        void loadRoles();
        void loadActions();
        return;
      }
      if (!alive.current) return;
      const code = detailOf(reply.body);
      if (reply.ok && !bindsTo(reply.body, a, modelVersionId)) {
        // A 2xx that does not name the row asked for: the outcome is unknown. Keep the attempt (an
        // assign retry reuses its key and replays; a revoke retry gets role_not_assigned) and re-read.
        setOutcome({
          kind: "error",
          error: uiError(tr("unboundOutcome", locale), "verify-state"),
          retry: true,
        });
      } else if (reply.ok) {
        attempt.current = null;
        setOutcome({
          kind: "notice",
          text: a.kind === "assign" ? "assigned" : "revokedNotice",
          role: a.role,
        });
      } else if (a.kind === "revoke" && reply.status === 409 && code === "role_not_assigned") {
        // A retry whose first request did land: the role IS revoked. Show that state, not an error.
        attempt.current = null;
        setOutcome({ kind: "notice", text: "alreadyRevoked", role: a.role });
      } else {
        attempt.current = null;
        const lead = tr(a.kind === "assign" ? "failedAssign" : "failedRevoke", locale);
        setOutcome({ kind: "error", error: replyError(lead, reply, locale), retry: false });
      }
      void loadRoles();
      void loadActions();
    } finally {
      flight.end();
    }
  }, [loadRoles, loadActions]);

  const openDialog = (kind: "assign" | "revoke", role: string, from: HTMLElement) => {
    setRestoreTo(from);
    setDialog({ kind, role });
  };
  const confirm = () => {
    if (!dialog) return;
    // A confirmed dialog is a NEW attempt: a new idempotency key for an assign.
    attempt.current =
      dialog.kind === "assign"
        ? { kind: "assign", role: dialog.role, key: newKey() }
        : { kind: "revoke", role: dialog.role };
    setRestoreTo(outcomeRef.current);
    setDialog(null);
    void run();
  };
  const closeDialog = (open: boolean) => {
    if (!open) setDialog(null);
  };

  // ---- render --------------------------------------------------------------------------------
  const rootStyle = { overflowWrap: "anywhere" as const, minInlineSize: 0, maxInlineSize: "100%" };
  const selStyle = { minBlockSize: "var(--target-min)", maxInlineSize: "100%" };
  const dlStyle = {
    display: "grid",
    gridTemplateColumns: "minmax(0, max-content) minmax(0, 1fr)",
    gap: "var(--space-2) var(--space-4)",
    margin: 0,
  };
  const ddStyle = { margin: 0, minInlineSize: 0, overflowWrap: "anywhere" as const };
  const busy = outcome.kind === "pending";
  const versionLabel =
    version.state === "ok" ? String(version.value.version_number) : modelVersionId;

  const lineage = (v: VersionItem) => {
    const isEndpoint = v.origin === "external_endpoint";
    return (
      <dl style={dlStyle} data-testid="lineage-facts">
        <dt>{tr("origin", locale)}</dt>
        <dd style={ddStyle} data-testid="lineage-origin">
          {labelOf(ORIGIN_KEY, v.origin, locale)} (<bdi>{v.origin}</bdi>)
        </dd>
        <dt>{tr("completeness", locale)}</dt>
        <dd style={ddStyle} data-testid="lineage-completeness">
          {labelOf(COMPLETENESS_KEY, v.lineage_completeness, locale)} (
          <bdi>{v.lineage_completeness}</bdi>)
        </dd>
        <dt>{tr("parent", locale)}</dt>
        <dd style={ddStyle} data-testid="lineage-parent">
          {v.parent_model_version_id ? (
            <bdi>{v.parent_model_version_id}</bdi>
          ) : (
            tr("unknown", locale)
          )}
        </dd>
        <dt>{tr("trainingData", locale)}</dt>
        <dd style={ddStyle} data-testid="lineage-training-data">
          {tr("unknown", locale)}
        </dd>
        <dt>{tr("digest", locale)}</dt>
        <dd style={ddStyle} data-testid="lineage-digest">
          {v.weights_digest ? (
            <bdi>{v.weights_digest}</bdi>
          ) : isEndpoint ? (
            tr("noArtifact", locale)
          ) : (
            tr("unknown", locale)
          )}
        </dd>
        {v.external_provider_model_ref ? (
          <>
            <dt>{tr("providerRef", locale)}</dt>
            <dd style={ddStyle} data-testid="lineage-provider-ref">
              <bdi>{v.external_provider_model_ref}</bdi>
            </dd>
          </>
        ) : null}
        {v.observed_deployment_fingerprint ? (
          <>
            <dt>{tr("fingerprint", locale)}</dt>
            <dd style={ddStyle} data-testid="lineage-fingerprint">
              <bdi>{v.observed_deployment_fingerprint}</bdi>
            </dd>
          </>
        ) : null}
      </dl>
    );
  };

  const loadError = (error: UiError, testId: string, reload: () => void) => (
    <div>
      <AlertMessage error={error} locale={locale} testId={testId} />
      <Button onClick={reload}>{tr("reload", locale)}</Button>
    </div>
  );

  const roleName = (r: string) => (Object.hasOwn(ROLE_KEY, r) ? tr(ROLE_KEY[r]!, locale) : r);

  const decisionCell = (d: Decision) =>
    d.allowed ? (
      <span data-testid={`decision-${d.action}`} data-allowed="true">
        <span aria-hidden="true">✓ </span>
        {tr("allowed", locale)}
      </span>
    ) : (
      <span data-testid={`decision-${d.action}`} data-allowed="false">
        <span aria-hidden="true">⊗ </span>
        {tr("notAvailable", locale)}
      </span>
    );
  const reasonCell = (d: Decision) => {
    if (d.allowed) {
      if (d.plan?.kind === "new_run")
        return <span data-testid={`plan-${d.action}`}>{tr("planNewRun", locale)}</span>;
      if (d.plan?.kind === "exact_resume")
        return (
          <span data-testid={`plan-${d.action}`}>
            {tr("planResumeBefore", locale)} <bdi>{d.plan.step}</bdi>
          </span>
        );
      return null;
    }
    if (d.code === null)
      return <span data-testid={`reason-${d.action}`}>{tr("noDecision", locale)}</span>;
    return (
      <span data-testid={`reason-${d.action}`}>
        {reasonText(d.code, locale)} (<bdi>{d.code}</bdi>)
      </span>
    );
  };

  return (
    <div lang={lang} dir={dir} style={rootStyle} data-testid="model-version-panel">
      <h2 id={`${uid}-title`}>
        {tr("title", locale)} <bdi data-testid="panel-version-label">{versionLabel}</bdi>
      </h2>

      <section aria-labelledby={`${uid}-lineage`} data-testid="lineage-section">
        <h3 id={`${uid}-lineage`}>{tr("lineage", locale)}</h3>
        {version.state === "loading" ? (
          <p role="status" aria-busy="true">
            {tr("loadingLineage", locale)}
          </p>
        ) : version.state === "error" ? (
          loadError(version.error, "lineage-error", () => void loadVersion())
        ) : (
          lineage(version.value)
        )}
      </section>

      <section aria-labelledby={`${uid}-roles`} data-testid="roles-section">
        <h3 id={`${uid}-roles`}>{tr("roles", locale)}</h3>
        <div ref={outcomeRef} tabIndex={-1} data-testid="role-outcome">
          {outcome.kind === "pending" ? (
            <p role="status" aria-busy="true">
              {phrase("working", locale)}
            </p>
          ) : outcome.kind === "notice" ? (
            <p role="status" data-testid="role-notice">
              {tr(outcome.text, locale)} {roleName(outcome.role)}
            </p>
          ) : outcome.kind === "error" ? (
            <>
              <AlertMessage error={outcome.error} locale={locale} testId="role-error" />
              {outcome.retry ? (
                <Button data-testid="role-retry" onClick={() => void run()} disabled={busy}>
                  {tr("tryAgain", locale)}
                </Button>
              ) : null}
            </>
          ) : null}
        </div>
        {roles.state === "loading" ? (
          <p role="status" aria-busy="true">
            {tr("loadingRoles", locale)}
          </p>
        ) : roles.state === "error" ? (
          loadError(roles.error, "roles-error", () => void loadRoles())
        ) : (
          <DataTable
            caption={tr("rolesCaption", locale)}
            rows={roles.value}
            rowKey={(r) => r.id}
            empty={
              <EmptyState
                title={tr("noRolesTitle", locale)}
                why={tr("noRolesWhy", locale)}
                action={tr("noRolesAction", locale)}
              />
            }
            columns={[
              { key: "role", header: tr("colRole", locale), render: (r) => roleName(r.role) },
              {
                key: "state",
                header: tr("colState", locale),
                render: (r) => (
                  <span data-testid={`role-state-${r.id}`}>
                    {r.revoked_at === null ? tr("active", locale) : tr("revoked", locale)}
                  </span>
                ),
              },
              {
                key: "by",
                header: tr("colAssignedBy", locale),
                render: (r) => <bdi>{r.assigned_by}</bdi>,
              },
              {
                key: "at",
                header: tr("colAssignedAt", locale),
                render: (r) => <bdi>{r.assigned_at}</bdi>,
              },
              {
                key: "revokedAt",
                header: tr("colRevokedAt", locale),
                render: (r) => (r.revoked_at === null ? "—" : <bdi>{r.revoked_at}</bdi>),
              },
              {
                key: "change",
                header: tr("colChange", locale),
                render: (r) =>
                  r.revoked_at === null ? (
                    <Button
                      variant="danger"
                      data-testid={`revoke-${r.role}`}
                      aria-label={`${tr("revoke", locale)}: ${roleName(r.role)}`}
                      disabled={busy}
                      onClick={(e) => openDialog("revoke", r.role, e.currentTarget)}
                    >
                      {tr("revoke", locale)}
                    </Button>
                  ) : (
                    "—"
                  ),
              },
            ]}
          />
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
          }}
          aria-labelledby={`${uid}-assign-legend`}
          style={{ display: "grid", gap: "var(--space-2)", marginBlockStart: "var(--space-3)" }}
        >
          <h4 id={`${uid}-assign-legend`} style={{ margin: 0 }}>
            {tr("assignLegend", locale)}
          </h4>
          <label htmlFor={`${uid}-role`}>{tr("roleSelect", locale)}</label>
          <select
            id={`${uid}-role`}
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            aria-describedby={`${uid}-role-help`}
            style={selStyle}
          >
            {MODEL_ROLES.map((r) => (
              <option key={r} value={r}>
                {roleName(r)}
              </option>
            ))}
          </select>
          <p id={`${uid}-role-help`} style={{ margin: 0 }}>
            {tr("roleHelp", locale)}
          </p>
          <div>
            <Button
              variant="primary"
              data-testid="assign-open"
              disabled={busy}
              onClick={(e) => openDialog("assign", choice, e.currentTarget)}
            >
              {tr("assign", locale)}
            </Button>
          </div>
        </form>
      </section>

      <section aria-labelledby={`${uid}-actions`} data-testid="actions-section">
        <h3 id={`${uid}-actions`}>{tr("actions", locale)}</h3>
        <p>{tr("actionsNote", locale)}</p>
        {actions.state === "loading" ? (
          <p role="status" aria-busy="true">
            {tr("loadingActions", locale)}
          </p>
        ) : actions.state === "error" ? (
          loadError(actions.error, "actions-error", () => void loadActions())
        ) : (
          <DataTable
            caption={tr("actionsCaption", locale)}
            rows={actions.value}
            rowKey={(d) => d.action}
            empty={null}
            columns={[
              {
                key: "action",
                header: tr("colAction", locale),
                render: (d) =>
                  Object.hasOwn(ACTION_KEY, d.action) ? (
                    tr(ACTION_KEY[d.action]!, locale)
                  ) : (
                    <bdi>{d.action}</bdi>
                  ),
              },
              { key: "decision", header: tr("colDecision", locale), render: decisionCell },
              { key: "reason", header: tr("colReason", locale), render: reasonCell },
            ]}
          />
        )}
      </section>

      <Dialog
        open={dialog !== null}
        onOpenChange={closeDialog}
        locale={locale}
        role={dialog?.kind === "revoke" ? "alertdialog" : "dialog"}
        title={tr(dialog?.kind === "revoke" ? "revokeTitle" : "assignTitle", locale)}
        restoreFocusTo={restoreTo}
        data-testid="role-dialog"
        description={
          dialog ? (
            <>
              {tr(dialog.kind === "assign" ? "assignBodyBefore" : "revokeBodyBefore", locale)}{" "}
              <strong data-testid="dialog-role">{roleName(dialog.role)}</strong>{" "}
              {tr(dialog.kind === "assign" ? "assignBodyMiddle" : "revokeBodyMiddle", locale)}{" "}
              <strong>
                <bdi data-testid="dialog-version">{versionLabel}</bdi>
              </strong>
              . {tr(dialog.kind === "assign" ? "assignBodyAfter" : "revokeBodyAfter", locale)}
            </>
          ) : (
            ""
          )
        }
      >
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
          <Button
            variant={dialog?.kind === "revoke" ? "danger" : "primary"}
            data-testid="dialog-confirm"
            onClick={confirm}
          >
            {tr(dialog?.kind === "revoke" ? "confirmRevoke" : "confirmAssign", locale)}
          </Button>
          <Button data-testid="dialog-cancel" onClick={() => setDialog(null)}>
            {tr("keep", locale)}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
