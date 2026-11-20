/**
 * Evaluation suite validation and locking UI (FTF-0603, Section 23-30/69/77/81).
 *
 * Consumes only frozen FTF-0601 / FTF-0602-P1 routes in
 * `packages/contracts/openapi/06-interface.yaml`:
 *
 *   GET   .../evaluation-suites/{suite}
 *   GET   .../evaluation-suites/{suite}/cases?after     (every page)
 *   POST  .../evaluation-suites/{suite}/lock
 *
 * Acceptance ("lock complete suite/rubric/dataset/runtime identity and access scope only after
 * validation"): the reviewer declares a sample plan (required critical categories and minimum
 * counts), the component loads every real case, validates the suite against that plan, computes
 * `suite_sha256` over the suite identity (id, agent manifest, version, purpose, grading logic)
 * and every case, and only then enables the lock. Immediately before the POST it reloads the cases
 * and recomputes the hash; a changed suite blocks the lock and asks for a fresh review.
 *
 * Negative acceptance ("missing critical categories, insufficient sample plan and unresolved
 * expected output block lock"): each of those is a named finding that disables the lock control
 * and is re-checked inside the lock handler itself, so a stale or scripted click cannot reach the
 * route. Findings name the case key / category and a code only; an expected output is never shown
 * (a final suite's answers must not leak). A 403 `final_suite_content_restricted` clears all case
 * content. Provider/network failures stay errors and never become a lock (Law 17); a lock reply
 * is accepted only when it is the same suite with `status: "locked"`.
 *
 * Disclosed, not hidden: the frozen contract has no persisted sample plan or `suite_sha256`
 * column (`database_changes`: none), so the plan lives in this browser session and the hash is
 * computed here and shown, not stored. The server's lock route remains the enforcement for
 * reviewer identity, independence and tenant scope; the plan and hash checks are enforced by
 * this UI path only, and no effect is authorized by a control alone. Persisting the plan/hash
 * server-side needs a separately claimed migration prerequisite.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button, EmptyState, Field, Pending, resolveLocale, dirFor, type Locale } from "../ui";

export const REFERENCE_FINAL_MIN_CASES = 1000;
export const REFERENCE_FINAL_MIN_CRITICAL = 100;
/** Bounded pagination: a cursor that repeats or a runaway listing is an error, never a loop. */
export const MAX_CASE_PAGES = 200;
export const MAX_PLAN_CATEGORIES = 100;

/** Section 27 / 69.2: graders whose verdict does not rest on model self-report. */
const INDEPENDENT_CRITICAL_GRADERS: readonly string[] = [
  "tool_name",
  "tool_arguments",
  "tool_sequence",
  "state_transition",
  "forbidden_action",
  "required_refusal",
];

/** A placeholder in an expected output means the answer is not decided yet. */
const UNRESOLVED_MARKERS: readonly string[] = [
  "",
  "todo",
  "tbd",
  "tbc",
  "unresolved",
  "fixme",
  "?",
];

export interface SuiteRecord {
  id: string;
  agent_manifest_id: string;
  version: number;
  status: "draft" | "locked";
  purpose: "development" | "final";
  name: string;
  grading_logic_version: number;
  reviewer_id: string | null;
  locked_at: string | null;
}

export interface SuiteCase {
  id: string;
  case_key: string;
  category: string;
  severity: string;
  critical: boolean;
  language: string;
  group_key: string;
  input_json: Record<string, unknown>;
  expected_json: Record<string, unknown>;
  grader_type: string;
  grader_config_json: Record<string, unknown>;
  weight: number;
}

export interface SamplePlan {
  requiredCategories: string[];
  minCases: number;
  minCritical: number;
  /** Minimum cases per required category (at least one critical among them is always required). */
  minPerCategory: number;
}

export type FindingCode =
  | "no_cases"
  | "missing_critical_category"
  | "insufficient_category_sample"
  | "insufficient_total_cases"
  | "insufficient_critical_cases"
  | "unresolved_expected_output"
  | "critical_oracle_not_independent"
  | "plan_invalid"
  | "plan_has_no_categories";

export interface Finding {
  code: FindingCode;
  /** The category or case_key the finding names; never an expected value. */
  subject: string | null;
  need?: number;
  have?: number;
}

export function defaultPlan(purpose: "development" | "final"): SamplePlan {
  return purpose === "final"
    ? {
        requiredCategories: [],
        minCases: REFERENCE_FINAL_MIN_CASES,
        minCritical: REFERENCE_FINAL_MIN_CRITICAL,
        minPerCategory: 1,
      }
    : { requiredCategories: [], minCases: 1, minCritical: 0, minPerCategory: 1 };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** True when any leaf of an expected output is null or a placeholder; an empty object/array too. */
export function hasUnresolvedExpected(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") {
    return UNRESOLVED_MARKERS.includes(value.trim().toLowerCase());
  }
  if (Array.isArray(value)) return value.length === 0 || value.some(hasUnresolvedExpected);
  if (isRecord(value)) {
    const entries = Object.values(value);
    return entries.length === 0 || entries.some(hasUnresolvedExpected);
  }
  return false;
}

export function validatePlan(plan: SamplePlan): Finding[] {
  const findings: Finding[] = [];
  const integers = [plan.minCases, plan.minCritical, plan.minPerCategory];
  if (!integers.every((n) => Number.isInteger(n) && n >= 0 && n <= 1_000_000)) {
    findings.push({ code: "plan_invalid", subject: null });
  }
  if (plan.minCases < 1 || plan.minPerCategory < 1) {
    findings.push({ code: "plan_invalid", subject: null });
  }
  if (plan.requiredCategories.length === 0) {
    findings.push({ code: "plan_has_no_categories", subject: null });
  }
  return findings;
}

/** Pure suite validation. Every finding blocks the lock; an empty list is the only pass. */
export function validateSuite(cases: readonly SuiteCase[], plan: SamplePlan): Finding[] {
  const findings: Finding[] = [...validatePlan(plan)];
  if (cases.length === 0) {
    findings.push({ code: "no_cases", subject: null });
    return findings;
  }
  if (cases.length < plan.minCases) {
    findings.push({
      code: "insufficient_total_cases",
      subject: null,
      need: plan.minCases,
      have: cases.length,
    });
  }
  const criticalTotal = cases.filter((c) => c.critical).length;
  if (criticalTotal < plan.minCritical) {
    findings.push({
      code: "insufficient_critical_cases",
      subject: null,
      need: plan.minCritical,
      have: criticalTotal,
    });
  }
  for (const category of plan.requiredCategories) {
    const inCategory = cases.filter((c) => c.category === category);
    if (!inCategory.some((c) => c.critical)) {
      findings.push({ code: "missing_critical_category", subject: category });
    }
    if (inCategory.length < plan.minPerCategory) {
      findings.push({
        code: "insufficient_category_sample",
        subject: category,
        need: plan.minPerCategory,
        have: inCategory.length,
      });
    }
  }
  for (const c of cases) {
    if (hasUnresolvedExpected(c.expected_json)) {
      findings.push({ code: "unresolved_expected_output", subject: c.case_key });
    }
    if (c.critical && !INDEPENDENT_CRITICAL_GRADERS.includes(c.grader_type)) {
      findings.push({ code: "critical_oracle_not_independent", subject: c.case_key });
    }
  }
  return findings;
}

/** Deterministic JSON: object keys sorted, no whitespace. Numbers/strings use JSON.stringify. */
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isRecord(value)) {
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

const K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/** FIPS 180-4 SHA-256 over the UTF-8 bytes of `text` (synchronous, no new dependency). */
export function sha256Hex(text: string): string {
  const bytes = new TextEncoder().encode(text);
  const bitLength = bytes.length * 8;
  const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6);
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000));
  view.setUint32(padded.length - 4, bitLength >>> 0);
  const h = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const w = new Uint32Array(64);
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  for (let offset = 0; offset < padded.length; offset += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4);
    for (let i = 16; i < 64; i++) {
      const w15 = w[i - 15] as number;
      const w2 = w[i - 2] as number;
      const s0 = rotr(w15, 7) ^ rotr(w15, 18) ^ (w15 >>> 3);
      const s1 = rotr(w2, 17) ^ rotr(w2, 19) ^ (w2 >>> 10);
      w[i] = ((w[i - 16] as number) + s0 + (w[i - 7] as number) + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, hh] = h as [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
    ];
    for (let i = 0; i < 64; i++) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + s1 + ch + (K[i] as number) + (w[i] as number)) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (s0 + maj) >>> 0;
      hh = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }
    const next = [a, b, c, d, e, f, g, hh];
    for (let i = 0; i < 8; i++) h[i] = ((h[i] as number) + (next[i] as number)) >>> 0;
  }
  return h.map((x) => x.toString(16).padStart(8, "0")).join("");
}

/** Hash over the locked identity: the suite's own identity fields and every case, in case_key
 * order, with only the fields that define what is graded (never server ids or timestamps). */
export function computeSuiteSha256(suite: SuiteRecord, cases: readonly SuiteCase[]): string {
  const ordered = [...cases].sort((x, y) =>
    x.case_key < y.case_key ? -1 : x.case_key > y.case_key ? 1 : 0,
  );
  const payload = {
    suite: {
      id: suite.id,
      agent_manifest_id: suite.agent_manifest_id,
      version: suite.version,
      purpose: suite.purpose,
      grading_logic_version: suite.grading_logic_version,
    },
    cases: ordered.map((c) => ({
      case_key: c.case_key,
      category: c.category,
      severity: c.severity,
      critical: c.critical,
      language: c.language,
      group_key: c.group_key,
      input_json: c.input_json,
      expected_json: c.expected_json,
      grader_type: c.grader_type,
      grader_config_json: c.grader_config_json,
      weight: c.weight,
    })),
  };
  return sha256Hex(canonicalJson(payload));
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function suiteUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  suiteId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/evaluation-suites/${suiteId}`
  );
}

// ---------------------------------------------------------------------------------------------
// Catalogue (en/ar). Findings and notices never contain submitted or expected values.
// ---------------------------------------------------------------------------------------------

const STRINGS = {
  heading: { en: "Validate and lock suite", ar: "التحقق من المجموعة وقفلها" },
  loadButton: { en: "Load suite and cases", ar: "تحميل المجموعة والحالات" },
  loading: { en: "Loading suite and cases…", ar: "جارٍ تحميل المجموعة والحالات…" },
  suiteSummary: {
    en: "Suite {name}, version {version}, purpose {purpose}, status {status}, {count} cases.",
    ar: "المجموعة {name}، الإصدار {version}، الغرض {purpose}، الحالة {status}، عدد الحالات {count}.",
  },
  planHeading: { en: "Sample plan", ar: "خطة العينات" },
  planCategories: {
    en: "Required critical categories (comma separated)",
    ar: "الفئات الحرجة المطلوبة (مفصولة بفواصل)",
  },
  planCategoriesHint: {
    en: "Each listed category needs at least one critical case.",
    ar: "تحتاج كل فئة مذكورة إلى حالة حرجة واحدة على الأقل.",
  },
  planMinCases: { en: "Minimum total cases", ar: "الحد الأدنى لإجمالي الحالات" },
  planMinCritical: { en: "Minimum critical cases", ar: "الحد الأدنى للحالات الحرجة" },
  planMinPerCategory: {
    en: "Minimum cases per required category",
    ar: "الحد الأدنى للحالات في كل فئة مطلوبة",
  },
  findingsHeading: { en: "Blocking findings", ar: "نتائج تمنع القفل" },
  passed: {
    en: "Validation passed. The suite can be locked.",
    ar: "نجح التحقق. يمكن قفل المجموعة.",
  },
  shaLabel: {
    en: "Computed suite_sha256 (this browser)",
    ar: "قيمة suite_sha256 المحسوبة (هذا المتصفح)",
  },
  lockButton: { en: "Lock suite", ar: "قفل المجموعة" },
  lockBlockedReason: {
    en: "Fix the blocking findings above to enable locking.",
    ar: "عالج النتائج المانعة أعلاه لتفعيل القفل.",
  },
  confirmWarning: {
    en: "Locking is permanent: the suite and its cases can no longer change. Confirm to lock it.",
    ar: "القفل دائم: لا يمكن بعد ذلك تغيير المجموعة أو حالاتها. أكّد للقفل.",
  },
  confirmButton: { en: "Confirm lock", ar: "تأكيد القفل" },
  cancelButton: { en: "Cancel", ar: "إلغاء" },
  locking: { en: "Locking…", ar: "جارٍ القفل…" },
  lockedNotice: {
    en: "Suite locked at {at}. Hash {sha} was computed from {count} cases.",
    ar: "تم قفل المجموعة في {at}. حُسبت القيمة {sha} من {count} حالة.",
  },
  alreadyLocked: {
    en: "This suite is already locked. No further change is possible.",
    ar: "هذه المجموعة مقفلة بالفعل. لا يمكن إجراء أي تغيير.",
  },
  restricted: {
    en: "Case content for this final suite is restricted. No case content is shown.",
    ar: "محتوى الحالات لهذه المجموعة النهائية مقيَّد. لا يُعرض أي محتوى للحالات.",
  },
  emptyTitle: { en: "No suite loaded", ar: "لم يتم تحميل مجموعة" },
  emptyWhy: {
    en: "Load the suite to validate its cases against a sample plan.",
    ar: "حمّل المجموعة للتحقق من حالاتها وفق خطة العينات.",
  },
  changedBeforeLock: {
    en: "The suite changed after you reviewed it. Nothing was locked. Review the new hash and lock again.",
    ar: "تغيّرت المجموعة بعد مراجعتك لها. لم يتم قفل أي شيء. راجع القيمة الجديدة ثم اقفل مجددًا.",
  },
  blockedBeforeLock: {
    en: "Validation failed on the latest cases. Nothing was locked. Fix the findings and load again.",
    ar: "فشل التحقق على أحدث الحالات. لم يتم قفل أي شيء. عالج النتائج ثم أعد التحميل.",
  },
  errGeneric: { en: "The request failed.", ar: "فشل الطلب." },
  errNotFound: { en: "The suite was not found.", ar: "لم يتم العثور على المجموعة." },
  errNotAuthenticated: { en: "You are not signed in.", ar: "لم تسجّل الدخول." },
  errRoleNotAuthorized: {
    en: "Your role is not allowed to do this.",
    ar: "دورك لا يسمح بتنفيذ هذا الإجراء.",
  },
  errLockForbidden: {
    en: "Only the suite's assigned reviewer can lock it.",
    ar: "يمكن للمراجع المعيَّن للمجموعة فقط قفلها.",
  },
  errLockRefused: {
    en: "The server refused the lock (the suite is already locked or incomplete).",
    ar: "رفض الخادم القفل (المجموعة مقفلة بالفعل أو غير مكتملة).",
  },
  errMalformed: {
    en: "The server reply was not in the expected form. Nothing was changed.",
    ar: "رد الخادم ليس بالشكل المتوقع. لم يتغير شيء.",
  },
  errUnreachable: {
    en: "The server could not be reached. Nothing was changed.",
    ar: "تعذّر الوصول إلى الخادم. لم يتغير شيء.",
  },
  errPaging: {
    en: "The case listing did not finish (repeated or runaway pages). Nothing was locked.",
    ar: "لم يكتمل سرد الحالات (صفحات مكررة أو لا نهائية). لم يتم قفل أي شيء.",
  },
  recoverRetry: { en: "Press Load again to retry.", ar: "اضغط تحميل للمحاولة مجددًا." },
  recoverAccess: {
    en: "Ask an administrator for access; repeating the request will not help.",
    ar: "اطلب الصلاحية من المسؤول؛ تكرار الطلب لن يفيد.",
  },
  recoverSignIn: { en: "Sign in again and retry.", ar: "سجّل الدخول مجددًا ثم أعد المحاولة." },
  recoverNoFix: {
    en: "Reload the suite to see its current state.",
    ar: "أعد تحميل المجموعة لعرض حالتها الحالية.",
  },
  fCode_no_cases: { en: "The suite has no cases.", ar: "لا توجد حالات في المجموعة." },
  fCode_missing_critical_category: {
    en: "Required category {subject} has no critical case.",
    ar: "الفئة المطلوبة {subject} لا تحتوي على حالة حرجة.",
  },
  fCode_insufficient_category_sample: {
    en: "Category {subject} has {have} cases; the plan needs {need}.",
    ar: "الفئة {subject} فيها {have} حالة؛ والخطة تتطلب {need}.",
  },
  fCode_insufficient_total_cases: {
    en: "The suite has {have} cases; the plan needs {need}.",
    ar: "في المجموعة {have} حالة؛ والخطة تتطلب {need}.",
  },
  fCode_insufficient_critical_cases: {
    en: "The suite has {have} critical cases; the plan needs {need}.",
    ar: "في المجموعة {have} حالة حرجة؛ والخطة تتطلب {need}.",
  },
  fCode_unresolved_expected_output: {
    en: "Case {subject} has an unresolved expected output.",
    ar: "الحالة {subject} لها ناتج متوقع غير محسوم.",
  },
  fCode_critical_oracle_not_independent: {
    en: "Critical case {subject} does not use an independent oracle.",
    ar: "الحالة الحرجة {subject} لا تستخدم مرجعًا مستقلًا.",
  },
  fCode_plan_invalid: {
    en: "The sample plan numbers must be whole numbers; totals and per-category counts at least 1.",
    ar: "يجب أن تكون أرقام خطة العينات أعدادًا صحيحة؛ والإجمالي والعدد لكل فئة 1 على الأقل.",
  },
  fCode_plan_has_no_categories: {
    en: "Declare at least one required critical category in the sample plan.",
    ar: "حدّد فئة حرجة مطلوبة واحدة على الأقل في خطة العينات.",
  },
} as const;

type Key = keyof typeof STRINGS;

export const SUITE_LOCK_STRING_KEYS = Object.keys(STRINGS) as Key[];
export const SUITE_LOCK_STRINGS: Record<Key, { readonly en: string; readonly ar: string }> =
  STRINGS;

function t(key: Key, locale: Locale | string, vars?: Record<string, string | number>): string {
  const entry = STRINGS[key];
  const template = entry[resolveLocale(locale)];
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    Object.hasOwn(vars, name) ? String(vars[name]) : whole,
  );
}

const SLOT = "\u0000";
function withBdi(
  key: Key,
  locale: Locale | string,
  name: string,
  value: string,
  vars = {},
): ReactNode {
  const [before, after] = t(key, locale, { ...vars, [name]: SLOT }).split(SLOT);
  return (
    <>
      {before}
      <bdi>{value}</bdi>
      {after}
    </>
  );
}

function findingNode(f: Finding, locale: Locale | string): ReactNode {
  const key = `fCode_${f.code}` as Key;
  const vars: Record<string, string | number> = {};
  if (f.need !== undefined) vars.need = f.need;
  if (f.have !== undefined) vars.have = f.have;
  if (f.subject === null) return t(key, locale, vars);
  return withBdi(key, locale, "subject", f.subject, vars);
}

const ERROR_DETAIL_KEY: Record<string, Key> = {
  not_found: "errNotFound",
  not_authenticated: "errNotAuthenticated",
  role_not_authorized: "errRoleNotAuthorized",
};

interface Failure {
  message: string;
  recovery: Key;
  restricted: boolean;
}

async function readFailure(
  response: Response,
  locale: Locale | string,
  lock: boolean,
): Promise<Failure> {
  let detail: string | null = null;
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") detail = body.detail;
  } catch {
    detail = null;
  }
  const status = response.status;
  if (detail === "final_suite_content_restricted") {
    return { message: t("restricted", locale), recovery: "recoverAccess", restricted: true };
  }
  let recovery: Key = "recoverRetry";
  if (status === 401) recovery = "recoverSignIn";
  else if (status === 403) recovery = "recoverAccess";
  else if (status === 404 || status === 409) recovery = "recoverNoFix";
  let message: string;
  if (lock && status === 403 && detail !== "role_not_authorized")
    message = t("errLockForbidden", locale);
  else if (lock && status === 409) message = t("errLockRefused", locale);
  else {
    const detailKey = detail === null ? undefined : ERROR_DETAIL_KEY[detail];
    message = t(detailKey ?? "errGeneric", locale);
  }
  return { message, recovery, restricted: false };
}

function isSuite(value: unknown): value is SuiteRecord {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.agent_manifest_id === "string" &&
    typeof value.version === "number" &&
    (value.status === "draft" || value.status === "locked") &&
    (value.purpose === "development" || value.purpose === "final") &&
    typeof value.name === "string" &&
    typeof value.grading_logic_version === "number" &&
    (value.reviewer_id === null || typeof value.reviewer_id === "string") &&
    (value.locked_at === null || typeof value.locked_at === "string")
  );
}

function isCase(value: unknown): value is SuiteCase {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.case_key === "string" &&
    typeof value.category === "string" &&
    typeof value.severity === "string" &&
    typeof value.critical === "boolean" &&
    typeof value.language === "string" &&
    typeof value.group_key === "string" &&
    isRecord(value.input_json) &&
    isRecord(value.expected_json) &&
    typeof value.grader_type === "string" &&
    isRecord(value.grader_config_json) &&
    typeof value.weight === "number"
  );
}

class LoadError extends Error {
  failure: Failure;
  constructor(failure: Failure) {
    super(failure.message);
    this.failure = failure;
  }
}

async function loadAll(
  baseSuiteUrl: string,
  locale: Locale | string,
): Promise<{ suite: SuiteRecord; cases: SuiteCase[] }> {
  const malformed = (): LoadError =>
    new LoadError({
      message: t("errMalformed", locale),
      recovery: "recoverRetry",
      restricted: false,
    });
  const get = async (url: string): Promise<unknown> => {
    let response: Response;
    try {
      response = await fetch(url, { method: "GET", credentials: "include" });
    } catch {
      throw new LoadError({
        message: t("errUnreachable", locale),
        recovery: "recoverRetry",
        restricted: false,
      });
    }
    if (!response.ok) throw new LoadError(await readFailure(response, locale, false));
    try {
      return await response.json();
    } catch {
      throw malformed();
    }
  };
  const suiteBody = await get(baseSuiteUrl);
  if (!isSuite(suiteBody)) throw malformed();
  const cases: SuiteCase[] = [];
  const seenCursors = new Set<string>();
  let after: string | null = null;
  for (let page = 0; page < MAX_CASE_PAGES; page++) {
    const query: string = after === null ? "" : `?after=${encodeURIComponent(after)}`;
    const body = await get(`${baseSuiteUrl}/cases${query}`);
    if (
      !isRecord(body) ||
      !Array.isArray(body.items) ||
      !body.items.every(isCase) ||
      !(body.next_after === null || typeof body.next_after === "string")
    ) {
      throw malformed();
    }
    cases.push(...(body.items as SuiteCase[]));
    if (body.next_after === null) {
      const keys = new Set(cases.map((c) => c.case_key));
      if (keys.size !== cases.length) throw malformed();
      return { suite: suiteBody, cases };
    }
    if (seenCursors.has(body.next_after)) {
      throw new LoadError({
        message: t("errPaging", locale),
        recovery: "recoverRetry",
        restricted: false,
      });
    }
    seenCursors.add(body.next_after);
    after = body.next_after;
  }
  throw new LoadError({
    message: t("errPaging", locale),
    recovery: "recoverRetry",
    restricted: false,
  });
}

function parseCategories(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const trimmed = part.trim();
    if (trimmed !== "") seen.add(trimmed);
  }
  return [...seen].slice(0, MAX_PLAN_CATEGORIES);
}

function parseCount(text: string): number {
  return /^\d{1,7}$/.test(text.trim()) ? Number(text.trim()) : Number.NaN;
}

export interface SuiteValidationLockingProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  suiteId: string;
  locale?: Locale;
}

function FocusableAlert({ children, testId }: { children: ReactNode; testId: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div ref={ref} tabIndex={-1} role="alert" data-testid={testId}>
      {children}
    </div>
  );
}

function ConfirmLock({
  warning,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
}: {
  warning: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div ref={ref} tabIndex={-1} role="group" aria-label={warning} data-testid="suite-lock-confirm">
      <p>{warning}</p>
      <Button
        type="button"
        variant="danger"
        data-testid="suite-lock-confirm-button"
        onClick={onConfirm}
      >
        {confirmLabel}
      </Button>{" "}
      <Button
        type="button"
        variant="secondary"
        data-testid="suite-lock-cancel-button"
        onClick={onCancel}
      >
        {cancelLabel}
      </Button>
    </div>
  );
}

export function SuiteValidationLocking({
  apiBaseUrl,
  organizationId,
  projectId,
  suiteId,
  locale = "en",
}: SuiteValidationLockingProps) {
  const lang = resolveLocale(locale);
  const dir = dirFor(lang);
  const idPrefix = useId();
  const baseSuiteUrl = suiteUrl(apiBaseUrl, organizationId, projectId, suiteId);

  const [suite, setSuite] = useState<SuiteRecord | null>(null);
  const [cases, setCases] = useState<SuiteCase[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [locking, setLocking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [restricted, setRestricted] = useState(false);
  const [error, setError] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<ReactNode>(null);
  const [categoriesText, setCategoriesText] = useState("");
  const [minCasesText, setMinCasesText] = useState("");
  const [minCriticalText, setMinCriticalText] = useState("");
  const [minPerCategoryText, setMinPerCategoryText] = useState("1");

  const defaults = defaultPlan(suite?.purpose ?? "development");
  const plan: SamplePlan = {
    requiredCategories: parseCategories(categoriesText),
    minCases: minCasesText.trim() === "" ? defaults.minCases : parseCount(minCasesText),
    minCritical: minCriticalText.trim() === "" ? defaults.minCritical : parseCount(minCriticalText),
    minPerCategory: parseCount(minPerCategoryText),
  };
  const findings: Finding[] | null =
    suite !== null && cases !== null && suite.status === "draft"
      ? validateSuite(cases, plan)
      : null;
  const sha = suite !== null && cases !== null ? computeSuiteSha256(suite, cases) : null;
  const canLock = findings !== null && findings.length === 0 && !locking && !loading;

  async function handleLoad() {
    setConfirming(false);
    setError(null);
    setNotice(null);
    setLoading(true);
    try {
      const loaded = await loadAll(baseSuiteUrl, locale);
      setRestricted(false);
      setSuite(loaded.suite);
      setCases(loaded.cases);
    } catch (caught) {
      setSuite(null);
      setCases(null);
      if (caught instanceof LoadError) {
        setRestricted(caught.failure.restricted);
        setError(caught.failure);
      } else {
        setError({
          message: t("errUnreachable", locale),
          recovery: "recoverRetry",
          restricted: false,
        });
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleLock() {
    if (suite === null || cases === null || sha === null) return;
    setConfirming(false);
    setError(null);
    setNotice(null);
    setLocking(true);
    try {
      // Re-load and re-validate immediately before the effect: the review the user saw must be
      // the suite that gets locked.
      const fresh = await loadAll(baseSuiteUrl, locale);
      const freshFindings =
        fresh.suite.status === "draft" ? validateSuite(fresh.cases, plan) : ["locked"];
      const freshSha = computeSuiteSha256(fresh.suite, fresh.cases);
      setSuite(fresh.suite);
      setCases(fresh.cases);
      if (freshFindings.length > 0) {
        setError({
          message: t("blockedBeforeLock", locale),
          recovery: "recoverNoFix",
          restricted: false,
        });
        return;
      }
      if (freshSha !== sha) {
        setError({
          message: t("changedBeforeLock", locale),
          recovery: "recoverNoFix",
          restricted: false,
        });
        return;
      }
      let response: Response;
      try {
        response = await fetch(`${baseSuiteUrl}/lock`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
      } catch {
        setError({
          message: t("errUnreachable", locale),
          recovery: "recoverRetry",
          restricted: false,
        });
        return;
      }
      if (!response.ok) {
        setError(await readFailure(response, locale, true));
        return;
      }
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        setError({
          message: t("errMalformed", locale),
          recovery: "recoverNoFix",
          restricted: false,
        });
        return;
      }
      if (
        !isSuite(body) ||
        body.id !== suiteId ||
        body.status !== "locked" ||
        body.locked_at === null
      ) {
        setError({
          message: t("errMalformed", locale),
          recovery: "recoverNoFix",
          restricted: false,
        });
        return;
      }
      setSuite(body);
      setNotice(
        withBdi("lockedNotice", locale, "sha", freshSha, {
          at: body.locked_at,
          count: fresh.cases.length,
        }),
      );
    } catch (caught) {
      if (caught instanceof LoadError) {
        setRestricted(caught.failure.restricted);
        if (caught.failure.restricted) {
          setSuite(null);
          setCases(null);
        }
        setError(caught.failure);
      } else {
        setError({
          message: t("errUnreachable", locale),
          recovery: "recoverRetry",
          restricted: false,
        });
      }
    } finally {
      setLocking(false);
    }
  }

  const loadButton = (
    <Button
      type="button"
      data-testid="suite-lock-load-button"
      onClick={handleLoad}
      disabled={loading || locking}
    >
      {t("loadButton", locale)}
    </Button>
  );

  const fieldId = (name: string) => `${idPrefix}-${name}`;

  return (
    <section
      lang={lang}
      dir={dir}
      aria-busy={loading || locking}
      style={{ overflowWrap: "anywhere" }}
    >
      <h2>{t("heading", locale)}</h2>
      {suite !== null && cases !== null ? loadButton : null}
      {loading ? <Pending label={t("loading", locale)} locale={locale} /> : null}

      {error ? (
        <FocusableAlert testId="suite-lock-error">
          <p>{error.message}</p>
          <p>{t(error.recovery, locale)}</p>
        </FocusableAlert>
      ) : null}
      {restricted ? <p data-testid="suite-lock-restricted">{t("restricted", locale)}</p> : null}
      {notice ? (
        <p role="status" data-testid="suite-lock-notice">
          {notice}
        </p>
      ) : null}

      {suite === null || cases === null ? (
        restricted ? null : (
          <EmptyState
            title={t("emptyTitle", locale)}
            why={t("emptyWhy", locale)}
            action={loadButton}
          />
        )
      ) : (
        <>
          <p data-testid="suite-lock-summary">
            {withBdi("suiteSummary", locale, "name", suite.name, {
              version: suite.version,
              purpose: suite.purpose,
              status: suite.status,
              count: cases.length,
            })}
          </p>
          {suite.status === "locked" ? (
            <p data-testid="suite-lock-already">{t("alreadyLocked", locale)}</p>
          ) : (
            <>
              <h3>{t("planHeading", locale)}</h3>
              <Field
                id={fieldId("categories")}
                data-testid="plan-categories"
                label={t("planCategories", locale)}
                helper={t("planCategoriesHint", locale)}
                requirement="required"
                locale={locale}
                value={categoriesText}
                onChange={(event) => setCategoriesText(event.target.value)}
              />
              <Field
                id={fieldId("min-cases")}
                data-testid="plan-min-cases"
                requirement="optional"
                locale={locale}
                label={t("planMinCases", locale)}
                inputMode="numeric"
                placeholder={String(defaults.minCases)}
                value={minCasesText}
                onChange={(event) => setMinCasesText(event.target.value)}
              />
              <Field
                id={fieldId("min-critical")}
                data-testid="plan-min-critical"
                requirement="optional"
                locale={locale}
                label={t("planMinCritical", locale)}
                inputMode="numeric"
                placeholder={String(defaults.minCritical)}
                value={minCriticalText}
                onChange={(event) => setMinCriticalText(event.target.value)}
              />
              <Field
                id={fieldId("min-per-category")}
                data-testid="plan-min-per-category"
                requirement="optional"
                locale={locale}
                label={t("planMinPerCategory", locale)}
                inputMode="numeric"
                value={minPerCategoryText}
                onChange={(event) => setMinPerCategoryText(event.target.value)}
              />
              {findings !== null && findings.length > 0 ? (
                <div data-testid="suite-lock-findings">
                  <h3>{t("findingsHeading", locale)}</h3>
                  <ul>
                    {findings.map((f, index) => (
                      <li key={`${f.code}-${f.subject ?? ""}-${index}`} data-finding={f.code}>
                        {findingNode(f, locale)}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p data-testid="suite-lock-passed">{t("passed", locale)}</p>
              )}
              <p>
                {t("shaLabel", locale)}: <code data-testid="suite-lock-sha">{sha}</code>
              </p>
              <Button
                type="button"
                data-testid="suite-lock-lock-button"
                onClick={() => setConfirming(true)}
                disabled={!canLock}
                disabledReason={
                  findings !== null && findings.length > 0
                    ? t("lockBlockedReason", locale)
                    : undefined
                }
              >
                {locking ? t("locking", locale) : t("lockButton", locale)}
              </Button>
              {confirming && canLock ? (
                <ConfirmLock
                  warning={t("confirmWarning", locale)}
                  confirmLabel={t("confirmButton", locale)}
                  cancelLabel={t("cancelButton", locale)}
                  onConfirm={handleLock}
                  onCancel={() => setConfirming(false)}
                />
              ) : null}
            </>
          )}
        </>
      )}
    </section>
  );
}
