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
 * Server enforcement (FTF-0603-F1): the lock request carries `{sample_plan, suite_sha256}` and the
 * server re-runs every clause itself, inside the locking transaction, refusing a lock with a typed
 * 409 finding per violated clause (`suite_lock_validation_failed`). The checks here are the same
 * rules run early so the reviewer sees them before the request; a control alone authorizes nothing.
 * A final suite starts from the whole Section 26 category list and the 1000 / 100 floors, which the
 * plan can raise but never lower. `suite_sha256` is the server's canonical hash (organization,
 * project, suite identity and every case), reproduced from the shared test vector.
 * If the lock request is sent and its result cannot be read, the outcome is reported as unknown
 * (verify the state), never as "nothing changed".
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import {
  AlertMessage,
  Button,
  Dialog,
  EmptyState,
  Field,
  Pending,
  dirFor,
  kindForStatus,
  resolveLocale,
  uiError,
  type Locale,
  type UiError,
} from "../ui";

/** The reference final suite floors (Section 69.3), the same numbers the server enforces. */
export const FINAL_MIN_INDEPENDENT_TASKS = 1000;
export const FINAL_MIN_CRITICAL_SCENARIOS = 100;
/** Bounded pagination: a cursor that repeats or a runaway listing is an error, never a loop. */
export const MAX_CASE_PAGES = 200;
/** The server refuses more than this many categories; the UI never truncates, it reports. */
export const MAX_PLAN_CATEGORIES = 200;
export const MAX_PLAN_COUNT = 1_000_000;
/** The server's `CATEGORY_MAX_LENGTH`: a longer category name is refused, never cut. */
export const CATEGORY_MAX_LENGTH = 100;
/** The canonical-JSON format tag the server hashes under (shared test vector). */
export const HASH_FORMAT = "sovra.evaluation_suite.sha256.v1";
const MAX_SAFE_INTEGER = 9_007_199_254_740_991;

/**
 * Section 26, verbatim and in the spec's order: a final suite must name every one of these in its
 * plan (the server's `SECTION_26_CATEGORIES`; a test pins the two lists together).
 */
export const SECTION_26_CATEGORIES: readonly string[] = [
  "happy-path tasks",
  "ambiguous input",
  "missing information",
  "invalid input",
  "incorrect user assumption",
  "tool failure",
  "tool timeout",
  "permission denied",
  "malformed tool result",
  "partial tool result",
  "conflicting tool results",
  "multi-turn task",
  "long context",
  "retry/recovery",
  "refusal",
  "prompt injection",
  "unauthorized tool use",
  "critical side-effect prevention",
  "schema compliance",
  "loop prevention",
  "termination correctness",
  "cost efficiency",
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

/** The wire shape of the lock request's `sample_plan` (exactly these four fields). */
export interface SamplePlan {
  required_categories: string[];
  min_independent_tasks: number;
  min_critical_scenarios: number;
  min_cases_per_category: number;
}

/** Every finding code a lock can be blocked on: the server's `FINDING_CODES`. */
export const FINDING_CODES = [
  "sample_plan_missing",
  "sample_plan_below_floor",
  "no_cases",
  "insufficient_independent_tasks",
  "insufficient_critical_scenarios",
  "missing_critical_category",
  "insufficient_category_sample",
  "unresolved_expected_output",
  "non_canonical_number",
  "suite_sha256_required",
  "suite_sha256_mismatch",
] as const;

export type FindingCode = (typeof FINDING_CODES)[number];

export interface Finding {
  code: FindingCode;
  /** The category, plan field or case_key the finding names; never an expected value. */
  subject: string | null;
  need?: number;
  have?: number;
}

/**
 * The plan a suite starts from. A final suite starts from the whole Section 26 list and the
 * reference floors; the floors and the Section 26 names cannot be lowered or removed (the server
 * refuses a plan that does), only raised or added to.
 */
export function defaultPlan(purpose: "development" | "final"): SamplePlan {
  return purpose === "final"
    ? {
        required_categories: [...SECTION_26_CATEGORIES],
        min_independent_tasks: FINAL_MIN_INDEPENDENT_TASKS,
        min_critical_scenarios: FINAL_MIN_CRITICAL_SCENARIOS,
        min_cases_per_category: 1,
      }
    : {
        required_categories: [],
        min_independent_tasks: 1,
        min_critical_scenarios: 0,
        min_cases_per_category: 1,
      };
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

const COUNT_FIELDS = [
  "min_independent_tasks",
  "min_critical_scenarios",
  "min_cases_per_category",
] as const;

/** Floors the plan itself must meet (server `plan_floor_findings`): purpose floors, then shape. */
export function validatePlan(plan: SamplePlan, purpose: "development" | "final"): Finding[] {
  const findings: Finding[] = [];
  const floors: Record<(typeof COUNT_FIELDS)[number], number> = {
    min_independent_tasks: 1,
    min_critical_scenarios: 0,
    min_cases_per_category: 1,
  };
  if (purpose === "final") {
    floors.min_independent_tasks = FINAL_MIN_INDEPENDENT_TASKS;
    floors.min_critical_scenarios = FINAL_MIN_CRITICAL_SCENARIOS;
    for (const category of SECTION_26_CATEGORIES) {
      if (!plan.required_categories.includes(category)) {
        findings.push({ code: "missing_critical_category", subject: category });
      }
    }
  }
  if (
    plan.required_categories.length > MAX_PLAN_CATEGORIES ||
    plan.required_categories.some((name) => name.length > CATEGORY_MAX_LENGTH)
  ) {
    findings.push({ code: "sample_plan_missing", subject: "required_categories" });
  }
  if (plan.required_categories.length === 0) {
    findings.push({
      code: "sample_plan_below_floor",
      subject: "required_categories",
      need: 1,
      have: 0,
    });
  }
  for (const name of COUNT_FIELDS) {
    const have = plan[name];
    if (!Number.isInteger(have) || have < 0 || have > MAX_PLAN_COUNT) {
      findings.push({ code: "sample_plan_missing", subject: name });
    } else if (have < floors[name]) {
      findings.push({ code: "sample_plan_below_floor", subject: name, need: floors[name], have });
    }
  }
  return findings;
}

function groups(cases: readonly SuiteCase[]): number {
  return new Set(cases.map((c) => c.group_key)).size;
}

function hasUnsafeInteger(value: unknown): boolean {
  if (typeof value === "number")
    return Number.isInteger(value) && Math.abs(value) > MAX_SAFE_INTEGER;
  if (Array.isArray(value)) return value.some(hasUnsafeInteger);
  if (isRecord(value)) return Object.values(value).some(hasUnsafeInteger);
  return false;
}

/**
 * Pure suite validation, mirroring the server's `content_findings` rule for rule: every count is
 * of distinct `group_key` values (Section 69.2: turns of one conversation, repeated seeds and
 * template near-duplicates are not independent tasks), never of rows. Every finding blocks the
 * lock; an empty list is the only pass. The server re-runs all of this on the lock request.
 */
export function validateSuite(
  cases: readonly SuiteCase[],
  plan: SamplePlan,
  purpose: "development" | "final",
): Finding[] {
  const findings: Finding[] = [...validatePlan(plan, purpose)];
  if (cases.length === 0) {
    findings.push({ code: "no_cases", subject: null });
    return findings;
  }
  const independent = groups(cases);
  if (independent < plan.min_independent_tasks) {
    findings.push({
      code: "insufficient_independent_tasks",
      subject: null,
      need: plan.min_independent_tasks,
      have: independent,
    });
  }
  const critical = groups(cases.filter((c) => c.critical));
  if (critical < plan.min_critical_scenarios) {
    findings.push({
      code: "insufficient_critical_scenarios",
      subject: null,
      need: plan.min_critical_scenarios,
      have: critical,
    });
  }
  for (const category of plan.required_categories) {
    const inCategory = cases.filter((c) => c.category === category);
    if (!inCategory.some((c) => c.critical)) {
      findings.push({ code: "missing_critical_category", subject: category });
    }
    const sample = groups(inCategory);
    if (sample < plan.min_cases_per_category) {
      findings.push({
        code: "insufficient_category_sample",
        subject: category,
        need: plan.min_cases_per_category,
        have: sample,
      });
    }
  }
  for (const c of cases) {
    if (hasUnresolvedExpected(c.expected_json)) {
      findings.push({ code: "unresolved_expected_output", subject: c.case_key });
    }
    if (
      hasUnsafeInteger(c.input_json) ||
      hasUnsafeInteger(c.expected_json) ||
      hasUnsafeInteger(c.grader_config_json) ||
      hasUnsafeInteger(c.weight)
    ) {
      findings.push({ code: "non_canonical_number", subject: c.case_key });
    }
  }
  return findings;
}

/**
 * Canonical JSON (RFC 8785 style, the same bytes as the server's `canonical_json`): object keys
 * sorted by UTF-16 code units (the default `Array.sort` order), ECMAScript number and string
 * serialization via `JSON.stringify`, no whitespace.
 */
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

/**
 * `suite_sha256`: SHA-256 over the canonical JSON of the organization, project, suite identity and
 * every case in case_key (UTF-16 code unit) order, only the fields that define what is graded
 * (never server ids, names, reviewers or timestamps). It is byte-for-byte the server's
 * `compute_suite_sha256`; `packages/contracts/test-vectors/evaluation-suite-sha256.json` is the
 * shared vector both sides must reproduce.
 */
export function computeSuiteSha256(
  scope: { organizationId: string; projectId: string },
  suite: SuiteRecord,
  cases: readonly SuiteCase[],
): string {
  const ordered = [...cases].sort((x, y) =>
    x.case_key < y.case_key ? -1 : x.case_key > y.case_key ? 1 : 0,
  );
  const document = {
    format: HASH_FORMAT,
    organization_id: scope.organizationId,
    project_id: scope.projectId,
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
  return sha256Hex(canonicalJson(document));
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
  purposeDevelopment: { en: "development", ar: "تطوير" },
  purposeFinal: { en: "final", ar: "نهائية" },
  statusDraft: { en: "draft", ar: "مسودة" },
  statusLocked: { en: "locked", ar: "مقفلة" },
  planHeading: { en: "Sample plan", ar: "خطة العينات" },
  planCategories: {
    en: "Required critical categories (comma or line separated)",
    ar: "الفئات الحرجة المطلوبة (مفصولة بفواصل أو أسطر)",
  },
  planCategoriesHint: {
    en: "Each listed category needs at least one critical case. A final suite must keep every Section 26 category; you can add more.",
    ar: "تحتاج كل فئة مذكورة إلى حالة حرجة واحدة على الأقل. يجب أن تحتفظ المجموعة النهائية بكل فئات القسم 26، ويمكنك إضافة المزيد.",
  },
  planMinTasks: { en: "Minimum independent tasks", ar: "الحد الأدنى للمهام المستقلة" },
  planMinTasksHint: {
    en: "Counted by distinct group key, not by case rows. A final suite needs at least 1000.",
    ar: "تُحسب بمفاتيح المجموعات المختلفة لا بصفوف الحالات. تحتاج المجموعة النهائية إلى 1000 على الأقل.",
  },
  planMinCritical: { en: "Minimum critical scenarios", ar: "الحد الأدنى للسيناريوهات الحرجة" },
  planMinCriticalHint: {
    en: "Counted by distinct group key among critical cases. A final suite needs at least 100.",
    ar: "تُحسب بمفاتيح المجموعات المختلفة بين الحالات الحرجة. تحتاج المجموعة النهائية إلى 100 على الأقل.",
  },
  planMinPerCategory: {
    en: "Minimum independent tasks per required category",
    ar: "الحد الأدنى للمهام المستقلة في كل فئة مطلوبة",
  },
  findingsHeading: { en: "Blocking findings", ar: "نتائج تمنع القفل" },
  serverFindingsHeading: { en: "The server refused the lock", ar: "رفض الخادم القفل" },
  passed: {
    en: "Validation passed. The suite can be locked.",
    ar: "نجح التحقق. يمكن قفل المجموعة.",
  },
  shaLabel: {
    en: "suite_sha256 computed from the loaded cases",
    ar: "قيمة suite_sha256 المحسوبة من الحالات المحمّلة",
  },
  lockButton: { en: "Lock suite", ar: "قفل المجموعة" },
  lockBlockedReason: {
    en: "Fix the blocking findings above to enable locking.",
    ar: "عالج النتائج المانعة أعلاه لتفعيل القفل.",
  },
  confirmTitle: { en: "Lock this suite permanently?", ar: "قفل هذه المجموعة بشكل دائم؟" },
  confirmWarning: {
    en: "Locking is permanent: the suite and its cases can no longer change. The server checks the plan and the hash again before it locks.",
    ar: "القفل دائم: لا يمكن بعد ذلك تغيير المجموعة أو حالاتها. يفحص الخادم الخطة والقيمة مجددًا قبل القفل.",
  },
  confirmButton: { en: "Confirm lock", ar: "تأكيد القفل" },
  cancelButton: { en: "Keep as draft", ar: "إبقاؤها مسودة" },
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
    en: "The server refused the lock (the suite is already locked or incomplete). The suite was not changed.",
    ar: "رفض الخادم القفل (المجموعة مقفلة بالفعل أو غير مكتملة). لم تتغير المجموعة.",
  },
  errLockFindings: {
    en: "The server refused the lock because of the findings below. The suite was not changed.",
    ar: "رفض الخادم القفل بسبب النتائج أدناه. لم تتغير المجموعة.",
  },
  errPlanRejected: {
    en: "The server rejected the sample plan field {field}. The suite was not changed.",
    ar: "رفض الخادم حقل خطة العينات {field}. لم تتغير المجموعة.",
  },
  errMalformed: {
    en: "The server reply was not in the expected form. Nothing was changed.",
    ar: "رد الخادم ليس بالشكل المتوقع. لم يتغير شيء.",
  },
  errUnreachable: {
    en: "The server could not be reached. Nothing was changed.",
    ar: "تعذّر الوصول إلى الخادم. لم يتغير شيء.",
  },
  errLockOutcomeUnknown: {
    en: "The lock request was sent, but its result could not be confirmed. The suite may now be locked.",
    ar: "أُرسل طلب القفل لكن تعذّر تأكيد نتيجته. قد تكون المجموعة مقفلة الآن.",
  },
  errPaging: {
    en: "The case listing did not finish (repeated or runaway pages). Nothing was locked.",
    ar: "لم يكتمل سرد الحالات (صفحات مكررة أو لا نهائية). لم يتم قفل أي شيء.",
  },
  fCode_sample_plan_missing: {
    en: "The sample plan field {subject} is missing or not a whole number from 0 to 1,000,000 (at most 200 categories, each up to 100 characters).",
    ar: "حقل خطة العينات {subject} مفقود أو ليس عددًا صحيحًا من 0 إلى 1,000,000 (200 فئة كحد أقصى، لكل منها 100 حرف كحد أقصى).",
  },
  fCode_sample_plan_below_floor: {
    en: "The plan field {subject} is {have}; it must be at least {need}.",
    ar: "قيمة حقل الخطة {subject} هي {have}؛ ويجب ألا تقل عن {need}.",
  },
  fCode_no_cases: { en: "The suite has no cases.", ar: "لا توجد حالات في المجموعة." },
  fCode_insufficient_independent_tasks: {
    en: "The suite has {have} independent tasks; the plan needs {need}.",
    ar: "في المجموعة {have} مهمة مستقلة؛ والخطة تتطلب {need}.",
  },
  fCode_insufficient_critical_scenarios: {
    en: "The suite has {have} critical scenarios; the plan needs {need}.",
    ar: "في المجموعة {have} سيناريو حرج؛ والخطة تتطلب {need}.",
  },
  fCode_missing_critical_category: {
    en: "Required category {subject} has no critical case.",
    ar: "الفئة المطلوبة {subject} لا تحتوي على حالة حرجة.",
  },
  fCode_insufficient_category_sample: {
    en: "Category {subject} has {have} independent tasks; the plan needs {need}.",
    ar: "الفئة {subject} فيها {have} مهمة مستقلة؛ والخطة تتطلب {need}.",
  },
  fCode_unresolved_expected_output: {
    en: "Case {subject} has an unresolved expected output.",
    ar: "الحالة {subject} لها ناتج متوقع غير محسوم.",
  },
  fCode_non_canonical_number: {
    en: "Case {subject} holds a whole number too large to hash exactly.",
    ar: "الحالة {subject} تحتوي على عدد صحيح أكبر من أن يُحسب له تجزئة دقيقة.",
  },
  fCode_suite_sha256_required: {
    en: "No reviewed hash was sent with the lock request.",
    ar: "لم تُرسل قيمة التجزئة المراجَعة مع طلب القفل.",
  },
  fCode_suite_sha256_mismatch: {
    en: "The suite changed after review: the reviewed hash is no longer its hash.",
    ar: "تغيّرت المجموعة بعد المراجعة: لم تعد القيمة المراجَعة هي قيمتها.",
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

/** The plan fields a finding can name, shown by their own label, never as a raw wire name. */
const PLAN_FIELD_KEY: Record<string, Key> = {
  required_categories: "planCategories",
  min_independent_tasks: "planMinTasks",
  min_critical_scenarios: "planMinCritical",
  min_cases_per_category: "planMinPerCategory",
};

function findingNode(f: Finding, locale: Locale | string): ReactNode {
  const key = `fCode_${f.code}` as Key;
  const vars: Record<string, string | number> = {};
  if (f.need !== undefined) vars.need = f.need;
  if (f.have !== undefined) vars.have = f.have;
  if (f.subject === null) return t(key, locale, vars);
  const fieldKey = Object.hasOwn(PLAN_FIELD_KEY, f.subject) ? PLAN_FIELD_KEY[f.subject] : undefined;
  const subject = fieldKey === undefined ? f.subject : t(fieldKey, locale);
  return withBdi(key, locale, "subject", subject, vars);
}

function purposeLabel(purpose: SuiteRecord["purpose"], locale: Locale | string): string {
  return t(purpose === "final" ? "purposeFinal" : "purposeDevelopment", locale);
}

function statusLabel(status: SuiteRecord["status"], locale: Locale | string): string {
  return t(status === "locked" ? "statusLocked" : "statusDraft", locale);
}

const ERROR_DETAIL_KEY: Record<string, Key> = {
  not_found: "errNotFound",
  not_authenticated: "errNotAuthenticated",
  role_not_authorized: "errRoleNotAuthorized",
};

interface Failure {
  error: UiError;
  restricted: boolean;
  /** Typed server findings of a 409 `suite_lock_validation_failed`, when the reply carried them. */
  findings?: Finding[];
}

function failure(message: string, kind: UiError["kind"] = "retry"): Failure {
  return { error: uiError(message, kind), restricted: false };
}

/** A finding only when every field has the server's shape; anything else is not a finding. */
function parseServerFindings(value: unknown): Finding[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const out: Finding[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.code !== "string") return null;
    if (!(FINDING_CODES as readonly string[]).includes(item.code)) return null;
    const subject = item.subject;
    if (!(subject === null || subject === undefined || typeof subject === "string")) return null;
    const need = item.need;
    const have = item.have;
    if (!(need === null || need === undefined || typeof need === "number")) return null;
    if (!(have === null || have === undefined || typeof have === "number")) return null;
    const finding: Finding = { code: item.code as FindingCode, subject: subject ?? null };
    if (typeof need === "number") finding.need = need;
    if (typeof have === "number") finding.have = have;
    out.push(finding);
  }
  return out;
}

async function readFailure(
  response: Response,
  locale: Locale | string,
  lock: boolean,
): Promise<Failure> {
  let detail: unknown = null;
  try {
    const body: unknown = await response.json();
    if (isRecord(body)) detail = body.detail;
  } catch {
    detail = null;
  }
  const status = response.status;
  const kind = kindForStatus(status);
  if (detail === "final_suite_content_restricted") {
    return { error: uiError(t("restricted", locale), "permission"), restricted: true };
  }
  if (
    lock &&
    status === 409 &&
    isRecord(detail) &&
    detail.code === "suite_lock_validation_failed"
  ) {
    const findings = parseServerFindings(detail.findings);
    if (findings !== null) {
      return {
        error: uiError(t("errLockFindings", locale), "conflict"),
        restricted: false,
        findings,
      };
    }
    return failure(t("errLockRefused", locale), "conflict");
  }
  if (lock && status === 400 && isRecord(detail) && detail.code === "invalid_lock_request") {
    const field = typeof detail.field === "string" ? detail.field : "sample_plan";
    return failure(t("errPlanRejected", locale, { field }), "input");
  }
  if (lock && status === 403 && detail !== "role_not_authorized") {
    return failure(t("errLockForbidden", locale), "permission");
  }
  if (lock && status === 409) return failure(t("errLockRefused", locale), "conflict");
  const detailKey = typeof detail === "string" ? ERROR_DETAIL_KEY[detail] : undefined;
  return failure(t(detailKey ?? "errGeneric", locale), kind);
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
    super(failure.error.message);
    this.failure = failure;
  }
}

async function loadAll(
  baseSuiteUrl: string,
  locale: Locale | string,
): Promise<{ suite: SuiteRecord; cases: SuiteCase[] }> {
  const malformed = (): LoadError => new LoadError(failure(t("errMalformed", locale)));
  const get = async (url: string): Promise<unknown> => {
    let response: Response;
    try {
      response = await fetch(url, { method: "GET", credentials: "include" });
    } catch {
      throw new LoadError(failure(t("errUnreachable", locale)));
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
      throw new LoadError(failure(t("errPaging", locale)));
    }
    seenCursors.add(body.next_after);
    after = body.next_after;
  }
  throw new LoadError(failure(t("errPaging", locale)));
}

/** Comma or line separated, trimmed, de-duplicated. Never truncated: too many is a finding. */
function parseCategories(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[,\n]/)) {
    const trimmed = part.trim();
    if (trimmed !== "") seen.add(trimmed);
  }
  return [...seen];
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

/** A failed lock or load: announced, focus moved to it, with the recovery step beside it. */
function FocusedAlert({ error, locale }: { error: UiError; locale: Locale }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // After a macrotask: closing the confirmation dialog schedules its own focus return to the
    // Lock button (Radix FocusScope), which would otherwise take focus back from the alert.
    const timer = setTimeout(() => {
      ref.current?.querySelector<HTMLElement>("[role=alert]")?.focus();
    }, 0);
    return () => clearTimeout(timer);
  }, [error]);
  return (
    <div ref={ref} data-testid="suite-lock-error-block">
      <AlertMessage error={error} locale={locale} testId="suite-lock-error" focusable />
    </div>
  );
}

function FindingList({
  findings,
  heading,
  locale,
  testId,
}: {
  findings: readonly Finding[];
  heading: string;
  locale: Locale;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <h3>{heading}</h3>
      <ul>
        {findings.map((f, index) => (
          <li key={`${f.code}-${f.subject ?? ""}-${index}`} data-finding={f.code}>
            {findingNode(f, locale)}
          </li>
        ))}
      </ul>
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
  const scope = { organizationId, projectId };
  const lockTriggerRef = useRef<HTMLElement | null>(null);

  const [suite, setSuite] = useState<SuiteRecord | null>(null);
  const [cases, setCases] = useState<SuiteCase[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [locking, setLocking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [restricted, setRestricted] = useState(false);
  const [error, setError] = useState<UiError | null>(null);
  const [serverFindings, setServerFindings] = useState<Finding[] | null>(null);
  const [notice, setNotice] = useState<ReactNode>(null);
  // `null` = the purpose default (a final suite starts from the whole Section 26 list and floors).
  const [categoriesText, setCategoriesText] = useState<string | null>(null);
  const [minTasksText, setMinTasksText] = useState<string | null>(null);
  const [minCriticalText, setMinCriticalText] = useState<string | null>(null);
  const [minPerCategoryText, setMinPerCategoryText] = useState<string | null>(null);

  const purpose = suite?.purpose ?? "development";
  const defaults = defaultPlan(purpose);
  const categoriesValue = categoriesText ?? defaults.required_categories.join(", ");
  const minTasksValue = minTasksText ?? String(defaults.min_independent_tasks);
  const minCriticalValue = minCriticalText ?? String(defaults.min_critical_scenarios);
  const minPerCategoryValue = minPerCategoryText ?? String(defaults.min_cases_per_category);
  const plan: SamplePlan = {
    required_categories: parseCategories(categoriesValue),
    min_independent_tasks: parseCount(minTasksValue),
    min_critical_scenarios: parseCount(minCriticalValue),
    min_cases_per_category: parseCount(minPerCategoryValue),
  };
  const findings: Finding[] | null =
    suite !== null && cases !== null && suite.status === "draft"
      ? validateSuite(cases, plan, suite.purpose)
      : null;
  const sha = suite !== null && cases !== null ? computeSuiteSha256(scope, suite, cases) : null;
  const canLock = findings !== null && findings.length === 0 && !locking && !loading;

  function resetPlan() {
    setCategoriesText(null);
    setMinTasksText(null);
    setMinCriticalText(null);
    setMinPerCategoryText(null);
  }

  async function handleLoad() {
    setConfirming(false);
    setError(null);
    setServerFindings(null);
    setNotice(null);
    setLoading(true);
    try {
      const loaded = await loadAll(baseSuiteUrl, locale);
      setRestricted(false);
      if (loaded.suite.purpose !== suite?.purpose) resetPlan();
      setSuite(loaded.suite);
      setCases(loaded.cases);
    } catch (caught) {
      setSuite(null);
      setCases(null);
      if (caught instanceof LoadError) {
        setRestricted(caught.failure.restricted);
        setError(caught.failure.error);
      } else {
        setError(uiError(t("errUnreachable", locale)));
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleLock() {
    if (suite === null || cases === null || sha === null) return;
    setConfirming(false);
    setError(null);
    setServerFindings(null);
    setNotice(null);
    setLocking(true);
    try {
      // Re-load and re-validate immediately before the effect: the review the user saw must be
      // the suite that gets locked.
      const fresh = await loadAll(baseSuiteUrl, locale);
      const freshFindings =
        fresh.suite.status === "draft"
          ? validateSuite(fresh.cases, plan, fresh.suite.purpose)
          : ["locked"];
      const freshSha = computeSuiteSha256(scope, fresh.suite, fresh.cases);
      setSuite(fresh.suite);
      setCases(fresh.cases);
      if (freshFindings.length > 0) {
        setError(uiError(t("blockedBeforeLock", locale), "conflict"));
        return;
      }
      if (freshSha !== sha) {
        setError(uiError(t("changedBeforeLock", locale), "conflict"));
        return;
      }
      let response: Response;
      try {
        response = await fetch(`${baseSuiteUrl}/lock`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sample_plan: plan, suite_sha256: freshSha }),
        });
      } catch {
        // The request may have reached the server: the outcome is unknown, never "nothing changed".
        setError(uiError(t("errLockOutcomeUnknown", locale), "verify-state"));
        return;
      }
      if (!response.ok) {
        const refused = await readFailure(response, locale, true);
        setError(refused.error);
        if (refused.findings) setServerFindings(refused.findings);
        return;
      }
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        setError(uiError(t("errLockOutcomeUnknown", locale), "verify-state"));
        return;
      }
      if (
        !isSuite(body) ||
        body.id !== suiteId ||
        body.status !== "locked" ||
        body.locked_at === null
      ) {
        setError(uiError(t("errLockOutcomeUnknown", locale), "verify-state"));
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
        setError(caught.failure.error);
      } else {
        setError(uiError(t("errUnreachable", locale)));
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

      {error ? <FocusedAlert error={error} locale={lang} /> : null}
      {serverFindings ? (
        <FindingList
          findings={serverFindings}
          heading={t("serverFindingsHeading", locale)}
          locale={lang}
          testId="suite-lock-server-findings"
        />
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
              purpose: purposeLabel(suite.purpose, locale),
              status: statusLabel(suite.status, locale),
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
                value={categoriesValue}
                onChange={(event) => setCategoriesText(event.target.value)}
              />
              <Field
                id={fieldId("min-tasks")}
                data-testid="plan-min-tasks"
                requirement="required"
                locale={locale}
                label={t("planMinTasks", locale)}
                helper={t("planMinTasksHint", locale)}
                inputMode="numeric"
                value={minTasksValue}
                onChange={(event) => setMinTasksText(event.target.value)}
              />
              <Field
                id={fieldId("min-critical")}
                data-testid="plan-min-critical"
                requirement="required"
                locale={locale}
                label={t("planMinCritical", locale)}
                helper={t("planMinCriticalHint", locale)}
                inputMode="numeric"
                value={minCriticalValue}
                onChange={(event) => setMinCriticalText(event.target.value)}
              />
              <Field
                id={fieldId("min-per-category")}
                data-testid="plan-min-per-category"
                requirement="required"
                locale={locale}
                label={t("planMinPerCategory", locale)}
                inputMode="numeric"
                value={minPerCategoryValue}
                onChange={(event) => setMinPerCategoryText(event.target.value)}
              />
              {findings !== null && findings.length > 0 ? (
                <FindingList
                  findings={findings}
                  heading={t("findingsHeading", locale)}
                  locale={lang}
                  testId="suite-lock-findings"
                />
              ) : (
                <p data-testid="suite-lock-passed">{t("passed", locale)}</p>
              )}
              <p>
                {t("shaLabel", locale)}: <code data-testid="suite-lock-sha">{sha}</code>
              </p>
              <Button
                type="button"
                data-testid="suite-lock-lock-button"
                onClick={(event) => {
                  lockTriggerRef.current = event.currentTarget;
                  setConfirming(true);
                }}
                disabled={!canLock}
                disabledReason={
                  findings !== null && findings.length > 0
                    ? t("lockBlockedReason", locale)
                    : undefined
                }
              >
                {locking ? t("locking", locale) : t("lockButton", locale)}
              </Button>
              <Dialog
                open={confirming && canLock}
                onOpenChange={setConfirming}
                title={t("confirmTitle", locale)}
                description={t("confirmWarning", locale)}
                locale={lang}
                data-testid="suite-lock-confirm"
                restoreFocusTo={lockTriggerRef.current}
              >
                <div style={{ display: "flex", gap: "var(--space-2)", justifyContent: "flex-end" }}>
                  <Button
                    type="button"
                    variant="secondary"
                    data-testid="suite-lock-cancel-button"
                    onClick={() => setConfirming(false)}
                  >
                    {t("cancelButton", locale)}
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    data-testid="suite-lock-confirm-button"
                    onClick={handleLock}
                  >
                    {t("confirmButton", locale)}
                  </Button>
                </div>
              </Dialog>
            </>
          )}
        </>
      )}
    </section>
  );
}
