/**
 * Dataset issue resolution UI (FTF-0516, Sections 6.10, 6.12, 6.24, 7A.10, 8.1 steps 16-18, 8.2, 9).
 *
 * Consumes only the frozen FTF-0516-P1 routes in `packages/contracts/openapi/05-interface.yaml`,
 * all under `.../organizations/{org}/projects/{project}/datasets/{dataset}`:
 *
 *   GET  quality-report       the latest report (counts, blocking flags, residual-risk disclosure)
 *   GET  issues               issues of the latest report, ordered by issue_id, `after` paging
 *   GET  issue-resolutions    the most recent decisions, with before/after evidence digests
 *   POST resolve-issue        one decision per issue: exclude | correct | new_version | waive
 *
 * Acceptance ("UI resolves record issue via exclusion/correction/new version with before/after
 * evidence and audit"): a decision is sent with the report id and resolution version the server
 * returned and, per issue, the `evidence_sha256` the server returned as
 * `expected_before_evidence_sha256`, so a stale view is refused by the server (409), never
 * overwritten. The before/after evidence digests, actor and role shown for a decision are the
 * server's own `DatasetIssueResolution` fields; nothing is inferred client-side. A successful
 * decision is shown only after the response parses strictly AND echoes the report, the issue and
 * the action that were sent.
 *
 * Negative acceptance ("Cannot edit locked data in place or mark a detector false positive without
 * recorded authorized rationale"): there is no control that edits a record, a dataset or a locked
 * version in place -- `correct` and `new_version` name a SUCCESSOR dataset id that the server
 * itself validates. Every action, `waive` (the false-positive decision) included, requires a
 * non-blank rationale of 1..2000 characters after trimming before the request is even sent, and
 * `waive` is offered only for a warning (never a blocking issue or a detector failure). Whether the
 * principal may waive is the route's role gate (403 `waive_not_permitted` is surfaced as an error);
 * a control here never authorizes the effect.
 *
 * Law 17: 400/403/404/409/413/5xx and network failure are each an error message, never a success.
 * Identity: every displayed result and every enabled action is tied to the exact
 * api/org/project/dataset requested; on any change the screen is cleared before the next commit and
 * in-flight responses are ignored. The idempotency key is reused only for a retry of an
 * identical request that did not conclude.
 *
 * Scope disclosed, not hidden (same precedent as the other dataset-factory components): no Next.js
 * `app/` page tree yet (FTF-H029), so this is DOM-tested (Vitest + React Testing Library + jsdom,
 * mocked `fetch`) and UNWIRED to a page.
 */

import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from "react";

import {
  AlertMessage,
  Button,
  EmptyState,
  Field,
  Pending,
  dirFor,
  kindForStatus,
  networkError,
  resolveLocale,
  uiError,
  useRefocusAfter,
  type Locale,
  type UiError,
} from "../ui";
import { SERVER_DETAIL_CODES, t0516, word, type Key0516 } from "./strings_0516";

export type IssueAction = "exclude" | "correct" | "new_version" | "waive";
export type IssueSeverity = "blocking" | "warning";
export type IssueState = "open" | "resolved" | "waived";

export interface DatasetIssueResolution {
  id: string;
  quality_report_id: string;
  issue_id: string;
  action: IssueAction;
  engine_decision: "resolved" | "waived";
  excluded_batch_positions: number[];
  successor_dataset_id: string | null;
  before_evidence_sha256: string;
  after_evidence_sha256: string | null;
  reason: string;
  actor_id: string;
  actor_role: "ml_engineer" | "workspace_admin" | "organization_admin";
  resolution_version_after: number;
  created_at: string;
}

export interface DatasetQualityIssue {
  issue_id: string;
  report_id: string;
  detector: string;
  category: string;
  severity: IssueSeverity;
  reason_code: string;
  message_sha256: string;
  batch_positions: number[];
  record_indices: Array<number | null>;
  evidence_digest: string | null;
  is_detector_failure: boolean;
  evidence_sha256: string;
  state: IssueState;
  resolution: DatasetIssueResolution | null;
}

export interface DatasetQualityReport {
  id: string;
  dataset_id: string;
  dataset_version: number;
  dataset_sha256: string;
  report_seq: number;
  report_sha256: string;
  detector_versions: Record<string, string>;
  detectors_reported: string[];
  issue_count: number;
  blocking_count: number;
  warning_count: number;
  resolution_version: number;
  is_clean: boolean;
  blocks_split: boolean;
  blocks_training: boolean;
  residual_risk_disclosure: string;
  created_at: string;
  updated_at: string;
}

export interface DatasetIssueResolutionUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  datasetId: string;
  /** System-string language (FTF-UX02 round 3); "ar" renders right-to-left. Default "en". */
  locale?: Locale;
}

const REASON_MAX = 2000;
const PAGE_SIZE = 50;
const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_TIME = /^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/;
const ACTIONS: IssueAction[] = ["exclude", "correct", "new_version", "waive"];
const ROLES = ["ml_engineer", "workspace_admin", "organization_admin"];

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

function datasetBase(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  datasetId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${encodeURIComponent(organizationId)}` +
    `/projects/${encodeURIComponent(projectId)}/datasets/${encodeURIComponent(datasetId)}`
  );
}

export function qualityReportUrl(base: string): string {
  return `${base}/quality-report`;
}
export function issuesUrl(base: string, after: string | null): string {
  return `${base}/issues?limit=${PAGE_SIZE}${after ? `&after=${encodeURIComponent(after)}` : ""}`;
}
export function resolutionsUrl(base: string): string {
  return `${base}/issue-resolutions?limit=${PAGE_SIZE}`;
}
export function resolveUrl(base: string): string {
  return `${base}/resolve-issue`;
}

/**
 * A failed HTTP reply as a localized, typed error. A known server `detail` code gets the catalogue
 * text; an unknown one shows the server's own text in English (the pre-UX02 behaviour) but never
 * under another locale, where it is replaced by a localized refusal (no English in an Arabic
 * render). `code` lets the caller react to a specific refusal without matching display text.
 */
async function readFailure(
  response: Response,
  locale: Locale,
): Promise<{ error: UiError; code: string | null }> {
  const kind = kindForStatus(response.status);
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") {
      const code = body.detail;
      if ((SERVER_DETAIL_CODES as readonly string[]).includes(code)) {
        return { error: uiError(t0516(code as Key0516, locale), kind), code };
      }
      const text = resolveLocale(locale) === "en" ? code : t0516("unknownDetail", locale);
      return { error: uiError(text, kind), code };
    }
  } catch {
    // fall through to the generic message
  }
  return {
    error: uiError(t0516("requestFailed", locale, { status: response.status }), kind),
    code: null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const own = Object.keys(value);
  return own.length === keys.length && keys.every((key) => key in value);
}
function isStr(value: unknown): value is string {
  return typeof value === "string";
}
function isSha(value: unknown): value is string {
  return typeof value === "string" && SHA256.test(value);
}
function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}
function isDateTime(value: unknown): value is string {
  return typeof value === "string" && DATE_TIME.test(value) && !Number.isNaN(Date.parse(value));
}
function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}
function isIntArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every(isCount);
}

export function parseResolution(body: unknown): DatasetIssueResolution | null {
  if (!isRecord(body)) return null;
  const keys = [
    "id",
    "quality_report_id",
    "issue_id",
    "action",
    "engine_decision",
    "excluded_batch_positions",
    "successor_dataset_id",
    "before_evidence_sha256",
    "after_evidence_sha256",
    "reason",
    "actor_id",
    "actor_role",
    "resolution_version_after",
    "created_at",
  ];
  if (!exactKeys(body, keys)) return null;
  const ok =
    isUuid(body.id) &&
    isUuid(body.quality_report_id) &&
    isSha(body.issue_id) &&
    isStr(body.action) &&
    (ACTIONS as string[]).includes(body.action) &&
    (body.engine_decision === "resolved" || body.engine_decision === "waived") &&
    isIntArray(body.excluded_batch_positions) &&
    (body.successor_dataset_id === null || isUuid(body.successor_dataset_id)) &&
    isSha(body.before_evidence_sha256) &&
    (body.after_evidence_sha256 === null || isSha(body.after_evidence_sha256)) &&
    isStr(body.reason) &&
    body.reason.trim() !== "" &&
    [...body.reason].length <= REASON_MAX &&
    isStr(body.actor_id) &&
    body.actor_id.trim() !== "" &&
    isStr(body.actor_role) &&
    ROLES.includes(body.actor_role) &&
    typeof body.resolution_version_after === "number" &&
    Number.isSafeInteger(body.resolution_version_after) &&
    body.resolution_version_after >= 1 &&
    isDateTime(body.created_at);
  return ok ? (body as unknown as DatasetIssueResolution) : null;
}

export function parseIssue(body: unknown): DatasetQualityIssue | null {
  if (!isRecord(body)) return null;
  const keys = [
    "issue_id",
    "report_id",
    "detector",
    "category",
    "severity",
    "reason_code",
    "message_sha256",
    "batch_positions",
    "record_indices",
    "evidence_digest",
    "is_detector_failure",
    "evidence_sha256",
    "state",
    "resolution",
  ];
  if (!exactKeys(body, keys)) return null;
  const resolution = body.resolution === null ? null : parseResolution(body.resolution);
  const ok =
    isSha(body.issue_id) &&
    isUuid(body.report_id) &&
    isStr(body.detector) &&
    isStr(body.category) &&
    (body.severity === "blocking" || body.severity === "warning") &&
    isStr(body.reason_code) &&
    /^[a-z][a-z0-9_]*$/.test(body.reason_code) &&
    isSha(body.message_sha256) &&
    isIntArray(body.batch_positions) &&
    Array.isArray(body.record_indices) &&
    body.record_indices.every((v) => v === null || isCount(v)) &&
    (body.evidence_digest === null || isSha(body.evidence_digest)) &&
    typeof body.is_detector_failure === "boolean" &&
    isSha(body.evidence_sha256) &&
    (body.state === "open" || body.state === "resolved" || body.state === "waived") &&
    (body.resolution === null ? true : resolution !== null) &&
    // A decided issue carries its decision; an open one does not.
    (body.state === "open" ? body.resolution === null : body.resolution !== null) &&
    // The nested decision is this issue's own, on this issue's report, and agrees with its state.
    (resolution === null ||
      (resolution.issue_id === body.issue_id &&
        resolution.quality_report_id === body.report_id &&
        resolution.engine_decision === body.state));
  return ok ? ({ ...body, resolution } as unknown as DatasetQualityIssue) : null;
}

export function parseReport(body: unknown): DatasetQualityReport | null {
  if (!isRecord(body)) return null;
  const keys = [
    "id",
    "dataset_id",
    "dataset_version",
    "dataset_sha256",
    "report_seq",
    "report_sha256",
    "detector_versions",
    "detectors_reported",
    "issue_count",
    "blocking_count",
    "warning_count",
    "resolution_version",
    "is_clean",
    "blocks_split",
    "blocks_training",
    "residual_risk_disclosure",
    "created_at",
    "updated_at",
  ];
  if (!exactKeys(body, keys)) return null;
  const versions = body.detector_versions;
  const ok =
    isUuid(body.id) &&
    isUuid(body.dataset_id) &&
    isCount(body.dataset_version) &&
    body.dataset_version >= 1 &&
    isSha(body.dataset_sha256) &&
    isCount(body.report_seq) &&
    body.report_seq >= 1 &&
    isSha(body.report_sha256) &&
    isRecord(versions) &&
    Object.values(versions).every(isStr) &&
    Array.isArray(body.detectors_reported) &&
    body.detectors_reported.every(isStr) &&
    isCount(body.issue_count) &&
    isCount(body.blocking_count) &&
    isCount(body.warning_count) &&
    isCount(body.resolution_version) &&
    typeof body.is_clean === "boolean" &&
    typeof body.blocks_split === "boolean" &&
    typeof body.blocks_training === "boolean" &&
    isStr(body.residual_risk_disclosure) &&
    body.residual_risk_disclosure !== "" &&
    isDateTime(body.created_at) &&
    isDateTime(body.updated_at);
  return ok ? (body as unknown as DatasetQualityReport) : null;
}

function parseIssuePage(
  body: unknown,
): { items: DatasetQualityIssue[]; next: string | null } | null {
  if (!isRecord(body) || !exactKeys(body, ["items", "next_after"]) || !Array.isArray(body.items)) {
    return null;
  }
  if (body.next_after !== null && !isSha(body.next_after)) return null;
  const items: DatasetQualityIssue[] = [];
  for (const raw of body.items) {
    const issue = parseIssue(raw);
    if (issue === null) return null;
    items.push(issue);
  }
  return { items, next: body.next_after as string | null };
}

function parseResolutionPage(body: unknown): DatasetIssueResolution[] | null {
  if (!isRecord(body) || !exactKeys(body, ["items"]) || !Array.isArray(body.items)) return null;
  const items: DatasetIssueResolution[] = [];
  for (const raw of body.items) {
    const item = parseResolution(raw);
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

function parseResolveResponse(body: unknown): {
  quality_report_id: string;
  resolution_version: number;
  replayed: boolean;
  items: DatasetIssueResolution[];
} | null {
  if (
    !isRecord(body) ||
    !exactKeys(body, ["quality_report_id", "resolution_version", "replayed", "items"])
  ) {
    return null;
  }
  const items = Array.isArray(body.items) ? body.items.map(parseResolution) : null;
  if (
    !isUuid(body.quality_report_id) ||
    typeof body.resolution_version !== "number" ||
    !Number.isSafeInteger(body.resolution_version) ||
    body.resolution_version < 1 ||
    typeof body.replayed !== "boolean" ||
    items === null ||
    items.length === 0 ||
    items.some((item) => item === null)
  ) {
    return null;
  }
  return {
    quality_report_id: body.quality_report_id,
    resolution_version: body.resolution_version,
    replayed: body.replayed,
    items: items as DatasetIssueResolution[],
  };
}

/** The rationale as it will be sent: trimmed, 1..2000 characters (code points), never blank. */
export function validReason(reason: string): string | null {
  const trimmed = reason.trim();
  if (trimmed === "") return null;
  return [...trimmed].length <= REASON_MAX ? trimmed : null;
}

/** Why the issue offers no decision at all, or null when it is open and decidable. */
export function undecidableReason(
  issue: DatasetQualityIssue,
  locale: Locale = "en",
): string | null {
  if (issue.state !== "open") {
    return t0516("alreadyDecided", locale, { state: word("state", issue.state, locale) });
  }
  if (issue.is_detector_failure) return t0516("detectorFailure", locale);
  return null;
}

/** Waive is the false-positive decision: a warning only, never blocking, never a detector failure. */
export function allowedActions(issue: DatasetQualityIssue): IssueAction[] {
  if (undecidableReason(issue) !== null) return [];
  return issue.severity === "warning" ? ACTIONS : ACTIONS.filter((action) => action !== "waive");
}

/** What a decision request asked for; the 2xx response must echo every field of it. */
export interface SentDecision {
  reportId: string;
  expectedVersion: number;
  issueId: string;
  action: IssueAction;
  beforeEvidence: string;
  reason: string;
  positions: number[];
  successor: string | null;
}

/**
 * The single response-binding check: EVERY field sent is compared with the decision returned, and
 * every server-derived field is checked against the action. A hand-picked subset does not close
 * this class (a swapped reason, positions, successor or version would be shown as recorded).
 */
export function decisionMatchesRequest(
  decision: DatasetIssueResolution,
  sent: SentDecision,
): boolean {
  const waive = sent.action === "waive";
  return (
    decision.quality_report_id === sent.reportId &&
    decision.issue_id === sent.issueId &&
    decision.action === sent.action &&
    decision.before_evidence_sha256 === sent.beforeEvidence &&
    decision.reason === sent.reason &&
    decision.engine_decision === (waive ? "waived" : "resolved") &&
    (waive ? decision.after_evidence_sha256 === null : decision.after_evidence_sha256 !== null) &&
    decision.successor_dataset_id === sent.successor &&
    decision.excluded_batch_positions.length === sent.positions.length &&
    decision.excluded_batch_positions.every((p, i) => p === sent.positions[i]) &&
    decision.resolution_version_after === sent.expectedVersion + 1
  );
}

interface Draft {
  action: IssueAction;
  reason: string;
  positions: number[];
  successor: string;
}

const EMPTY_DRAFT: Draft = { action: "exclude", reason: "", positions: [], successor: "" };

interface Loaded {
  report: DatasetQualityReport;
  issues: DatasetQualityIssue[];
  next: string | null;
  resolutions: DatasetIssueResolution[];
}

/** Positions as they are sent: strictly increasing whatever the click order (toggling never duplicates). */
function sentPositions(draft: Draft): number[] {
  return [...draft.positions].sort((a, b) => a - b);
}

function newKey(): string {
  return `ftf-0516-${globalThis.crypto.randomUUID()}`;
}

/** A server-supplied identifier (digest, id, code, timestamp): data, never translated, isolated LTR and wrapped. */
function Data({ children }: { children: ReactNode }) {
  return (
    <bdi dir="ltr" style={{ overflowWrap: "anywhere" }}>
      {children}
    </bdi>
  );
}

/** Renders a catalogue template with ReactNode values at its `{name}` slots (the text is the template's). */
function Tpl({ k, locale, vars }: { k: Key0516; locale: Locale; vars: Record<string, ReactNode> }) {
  const parts = t0516(k, locale).split(/(\{\w+\})/g);
  return (
    <>
      {parts.map((part, i) => {
        const name = /^\{(\w+)\}$/.exec(part)?.[1];
        return name !== undefined && Object.hasOwn(vars, name) ? (
          <Fragment key={i}>{vars[name]}</Fragment>
        ) : (
          part
        );
      })}
    </>
  );
}

const WRAP = { overflowWrap: "anywhere", minInlineSize: 0 } as const;
const CONTROL_STYLE = {
  boxSizing: "border-box",
  inlineSize: "100%",
  maxInlineSize: "100%",
  minBlockSize: "var(--target-min)",
} as const;
const BLOCK_LABEL = { display: "block", marginBlockEnd: "var(--space-1)" } as const;

export function DatasetIssueResolutionUI({
  apiBaseUrl,
  organizationId,
  projectId,
  datasetId,
  locale = "en",
}: DatasetIssueResolutionUIProps) {
  const identity = JSON.stringify([apiBaseUrl, organizationId, projectId, datasetId]);
  const base = datasetBase(apiBaseUrl, organizationId, projectId, datasetId);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [recorded, setRecorded] = useState<DatasetIssueResolution | null>(null);
  const latestRequest = useRef(0);
  const attempt = useRef<{ body: string; key: string } | null>(null);
  const t = (key: Key0516, vars?: Record<string, string | number>) => t0516(key, locale, vars);
  const problemsId = useId();
  const rationaleId = useId();
  const actionId = useId();

  // A control that disables itself while its request is in flight loses focus to <body>; give it
  // back (UX01 `useRefocusAfter`), and move focus to the outcome (the failure alert, or the
  // recorded decision) so a keyboard user is never dropped on <body> after a failed or finished request.
  const armRefocus = useRefocusAfter(loading || submitting);
  useEffect(() => {
    if (error) document.querySelector<HTMLElement>('[data-testid="dir-error"]')?.focus();
  }, [error]);
  useEffect(() => {
    if (recorded) {
      document.querySelector<HTMLElement>('[data-testid="recorded-decision"]')?.focus();
    }
  }, [recorded]);

  // A different api/org/project/dataset invalidates everything before the next commit (no frame
  // with a stale decision control) and every in-flight response (its request number is stale).
  const [seenIdentity, setSeenIdentity] = useState(identity);
  if (seenIdentity !== identity) {
    setSeenIdentity(identity);
    setLoaded(null);
    setError(null);
    setLoading(false);
    setSelected(null);
    setDraft(EMPTY_DRAFT);
    setSubmitting(false);
    setRecorded(null);
  }
  useEffect(() => {
    latestRequest.current += 1;
    attempt.current = null;
  }, [identity]);

  async function fetchJson(
    url: string,
  ): Promise<{ ok: true; body: unknown } | { ok: false; error: UiError }> {
    const response = await fetch(url, { method: "GET", credentials: "include" });
    if (!response.ok) return { ok: false, error: (await readFailure(response, locale)).error };
    return { ok: true, body: await response.json() };
  }

  async function load(): Promise<boolean> {
    const request = ++latestRequest.current;
    const stale = () => request !== latestRequest.current;
    setLoading(true);
    setError(null);
    try {
      const [reportReply, issuesReply, resolutionsReply] = await Promise.all([
        fetchJson(qualityReportUrl(base)),
        fetchJson(issuesUrl(base, null)),
        fetchJson(resolutionsUrl(base)),
      ]);
      if (stale()) return false;
      for (const reply of [reportReply, issuesReply, resolutionsReply]) {
        if (!reply.ok) {
          setLoaded(null);
          setError(reply.error);
          return false;
        }
      }
      if (!reportReply.ok || !issuesReply.ok || !resolutionsReply.ok) return false;
      const report = parseReport(reportReply.body);
      const page = parseIssuePage(issuesReply.body);
      const resolutions = parseResolutionPage(resolutionsReply.body);
      if (report === null || page === null || resolutions === null) {
        setLoaded(null);
        setError(uiError(t("malformed")));
        return false;
      }
      if (
        report.dataset_id !== datasetId ||
        page.items.some((issue) => issue.report_id !== report.id)
      ) {
        setLoaded(null);
        setError(uiError(t("mismatch")));
        return false;
      }
      setLoaded({ report, issues: page.items, next: page.next, resolutions });
      return true;
    } catch (caught) {
      if (!stale()) {
        setLoaded(null);
        setError(networkError(caught, locale));
      }
      return false;
    } finally {
      if (!stale()) setLoading(false);
    }
  }

  async function loadMore() {
    if (!loaded || loaded.next === null) return;
    const request = ++latestRequest.current;
    const stale = () => request !== latestRequest.current;
    setLoading(true);
    setError(null);
    try {
      const reply = await fetchJson(issuesUrl(base, loaded.next));
      if (stale()) return;
      if (!reply.ok) {
        setError(reply.error);
        return;
      }
      const page = parseIssuePage(reply.body);
      if (page === null) {
        setError(uiError(t("malformed")));
        return;
      }
      if (page.items.some((issue) => issue.report_id !== loaded.report.id)) {
        setError(uiError(t("mismatch")));
        return;
      }
      setLoaded({ ...loaded, issues: [...loaded.issues, ...page.items], next: page.next });
    } catch (caught) {
      if (!stale()) setError(networkError(caught, locale));
    } finally {
      if (!stale()) setLoading(false);
    }
  }

  const issue =
    loaded && selected ? (loaded.issues.find((i) => i.issue_id === selected) ?? null) : null;
  const reason = validReason(draft.reason);
  const successorOk = isUuid(draft.successor.trim());
  // The server stores and echoes UUIDs lowercase; send the canonical form so an uppercase-typed
  // successor is not reported as a mismatched response.
  const successorSent = draft.successor.trim().toLowerCase();
  const needsSuccessor = draft.action === "correct" || draft.action === "new_version";
  const problems: string[] = [];
  if (issue) {
    if (!allowedActions(issue).includes(draft.action)) problems.push(t("problemAction"));
    if (reason === null) {
      problems.push(
        draft.reason.trim() === ""
          ? t("problemRationaleRequired")
          : t("problemRationaleTooLong", { max: REASON_MAX }),
      );
    }
    if (draft.action === "exclude" && draft.positions.length === 0) {
      problems.push(t("problemPositions"));
    }
    if (needsSuccessor && !successorOk) problems.push(t("problemSuccessor"));
  }
  // A submit during a reload would supersede it (stuck "Loading..."); decide on settled data only.
  const canSubmit = !!issue && !!loaded && problems.length === 0 && !submitting && !loading;

  function buildBody(report: DatasetQualityReport, target: DatasetQualityIssue): string {
    const item: Record<string, unknown> = {
      issue_id: target.issue_id,
      action: draft.action,
      reason: reason as string,
      expected_before_evidence_sha256: target.evidence_sha256,
    };
    if (draft.action === "exclude") {
      item.excluded_batch_positions = sentPositions(draft);
    }
    if (needsSuccessor) item.successor_dataset_id = successorSent;
    return JSON.stringify({
      quality_report_id: report.id,
      expected_resolution_version: report.resolution_version,
      resolutions: [item],
    });
  }

  async function handleSubmit() {
    if (!canSubmit || !loaded || !issue) return;
    const report = loaded.report;
    const sent = issue;
    const sentAction = draft.action;
    const bodyText = buildBody(report, sent);
    const expected: SentDecision = {
      reportId: report.id,
      expectedVersion: report.resolution_version,
      issueId: sent.issue_id,
      action: sentAction,
      beforeEvidence: sent.evidence_sha256,
      reason: reason as string,
      positions: sentAction === "exclude" ? sentPositions(draft) : [],
      successor: needsSuccessor ? successorSent : null,
    };
    // The same key only for a retry of an identical request that did not conclude.
    if (attempt.current === null || attempt.current.body !== bodyText) {
      attempt.current = { body: bodyText, key: newKey() };
    }
    const key = attempt.current.key;
    const request = ++latestRequest.current;
    const stale = () => request !== latestRequest.current;
    armRefocus(() => document.querySelector<HTMLElement>('[data-testid="dir-error"]'));
    setSubmitting(true);
    setError(null);
    setRecorded(null);
    try {
      const response = await fetch(resolveUrl(base), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...JSON.parse(bodyText), idempotency_key: key }),
      });
      if (!response.ok) {
        const failure = await readFailure(response, locale);
        if (!stale()) {
          if (
            failure.code === "idempotency_key_reused" ||
            /idempotency_key_reused|already used/.test(failure.error.message)
          ) {
            attempt.current = null;
          }
          setError(failure.error);
        }
        return;
      }
      const parsed = parseResolveResponse(await response.json());
      if (stale()) return;
      const decision = parsed?.items[0] ?? null;
      if (
        parsed === null ||
        parsed.quality_report_id !== report.id ||
        // A replay returns the report's CURRENT version, which later decisions on other issues may
        // have raised; only a fresh decision lands exactly on expected + 1. The decision's own
        // resolution_version_after is bound to expected + 1 either way.
        (parsed.replayed
          ? parsed.resolution_version < expected.expectedVersion + 1
          : parsed.resolution_version !== expected.expectedVersion + 1) ||
        parsed.items.length !== 1 ||
        decision === null ||
        !decisionMatchesRequest(decision, expected)
      ) {
        setError(uiError(t("resolutionMismatch"), "verify-state"));
        return;
      }
      attempt.current = null;
      setRecorded(decision);
      setSelected(null);
      // Reload so the state, evidence and resolution version shown are the server's, not ours.
      setSubmitting(false);
      await load();
      return;
    } catch (caught) {
      if (!stale()) setError(networkError(caught, locale, { write: true }));
    } finally {
      if (!stale()) setSubmitting(false);
    }
  }

  function togglePosition(position: number) {
    setDraft((d) => ({
      ...d,
      positions: d.positions.includes(position)
        ? d.positions.filter((p) => p !== position)
        : [...d.positions, position],
    }));
  }

  // Identifiers are isolated LTR only where a right-to-left run could reorder them; in LTR they stay
  // plain text nodes (the certified English suite matches the issue line as one text run).
  const d = (value: ReactNode) => (resolveLocale(locale) === "ar" ? <Data>{value}</Data> : value);
  const notRecorded = (value: unknown) =>
    value === null || value === undefined || value === "" ? t("notRecorded") : String(value);
  const undecidable = issue ? undecidableReason(issue, locale) : null;
  const rationaleProblem = problems.find(
    (p) =>
      p === t("problemRationaleRequired") ||
      p === t("problemRationaleTooLong", { max: REASON_MAX }),
  );

  return (
    <section
      aria-label={t("sectionLabel")}
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
      style={{ display: "grid", gap: "var(--space-3)", ...WRAP }}
    >
      <h2 style={WRAP}>{t("heading")}</h2>
      {!loaded && !loading && !error ? <p style={WRAP}>{t("intro")}</p> : null}
      <div>
        <Button
          variant="primary"
          data-testid="load-report-button"
          onClick={() => {
            armRefocus();
            void load();
          }}
          disabled={loading || submitting}
        >
          {loading ? t("loadingButton") : t("loadButton")}
        </Button>
      </div>
      {loading ? <Pending label={t("loadingReport")} locale={locale} /> : null}
      {submitting ? <Pending label={t("submittingStatus")} locale={locale} /> : null}
      {error ? <AlertMessage error={error} locale={locale} testId="dir-error" focusable /> : null}
      {recorded ? (
        <div
          role="status"
          tabIndex={-1}
          data-testid="recorded-decision"
          style={{
            ...WRAP,
            borderInlineStart: "4px solid var(--color-success)",
            paddingInlineStart: "var(--space-3)",
          }}
        >
          <p>
            <Tpl
              k="recordedHeadline"
              locale={locale}
              vars={{
                action: word("action", recorded.action, locale),
                engine: word("state", recorded.engine_decision, locale),
                actor: d(recorded.actor_id),
                role: word("role", recorded.actor_role, locale),
              }}
            />
          </p>
          <p>
            <Tpl
              k="recordedEvidence"
              locale={locale}
              vars={{
                before: d(recorded.before_evidence_sha256),
                after: d(notRecorded(recorded.after_evidence_sha256)),
              }}
            />
          </p>
        </div>
      ) : null}
      {loaded ? (
        <div style={{ display: "grid", gap: "var(--space-3)", minInlineSize: 0 }}>
          <dl
            data-testid="report"
            style={{ display: "grid", gap: "var(--space-1)", margin: 0, ...WRAP }}
          >
            <dt>{t("reportVersion")}</dt>
            <dd style={{ margin: 0 }}>{loaded.report.dataset_version}</dd>
            <dt>{t("reportReport")}</dt>
            <dd style={{ margin: 0 }}>
              <Tpl
                k="reportValue"
                locale={locale}
                vars={{
                  seq: loaded.report.report_seq,
                  sha: d(loaded.report.report_sha256),
                }}
              />
            </dd>
            <dt>{t("reportIssues")}</dt>
            <dd style={{ margin: 0 }}>
              {t("issuesValue", {
                count: loaded.report.issue_count,
                blocking: loaded.report.blocking_count,
                warning: loaded.report.warning_count,
              })}
            </dd>
            <dt>{t("reportBlocksSplit")}</dt>
            <dd style={{ margin: 0 }}>{loaded.report.blocks_split ? t("yes") : t("no")}</dd>
            <dt>{t("reportBlocksTraining")}</dt>
            <dd style={{ margin: 0 }}>{loaded.report.blocks_training ? t("yes") : t("no")}</dd>
            <dt>{t("reportResolutionVersion")}</dt>
            <dd style={{ margin: 0 }}>{loaded.report.resolution_version}</dd>
          </dl>
          <p data-testid="residual-risk" style={WRAP}>
            {d(loaded.report.residual_risk_disclosure)}
          </p>
          {loaded.issues.length === 0 && loaded.next === null ? (
            <EmptyState
              title={t("emptyIssuesTitle")}
              why={t("emptyIssuesWhy")}
              action={
                <Button
                  data-testid="empty-reload-button"
                  onClick={() => {
                    armRefocus();
                    void load();
                  }}
                  disabled={loading || submitting}
                >
                  {t("reloadButton")}
                </Button>
              }
            />
          ) : (
            <ul
              aria-label={t("issuesList")}
              style={{
                display: "grid",
                gap: "var(--space-2)",
                margin: 0,
                padding: 0,
                listStyle: "none",
              }}
            >
              {loaded.issues.map((entry) => (
                <li
                  key={entry.issue_id}
                  data-testid={`issue-${entry.issue_id}`}
                  style={{
                    display: "grid",
                    gap: "var(--space-1)",
                    padding: "var(--space-2)",
                    border: "1px solid var(--color-border)",
                    borderInlineStart: `4px solid ${entry.severity === "blocking" ? "var(--color-danger)" : "var(--color-muted)"}`,
                    ...WRAP,
                  }}
                >
                  <span>
                    <Tpl
                      k="issueLine"
                      locale={locale}
                      vars={{
                        severity: word("severity", entry.severity, locale),
                        detector: d(word("category", entry.detector, locale)),
                        category: d(word("category", entry.category, locale)),
                        reason: d(entry.reason_code),
                        state: word("state", entry.state, locale),
                      }}
                    />
                  </span>
                  <span>
                    <Tpl
                      k="positionsLine"
                      locale={locale}
                      vars={{
                        list: entry.batch_positions.join(", ") || t("none"),
                      }}
                    />
                  </span>
                  {entry.resolution ? (
                    <span data-testid={`evidence-${entry.issue_id}`}>
                      <Tpl
                        k="decisionLine"
                        locale={locale}
                        vars={{
                          before: d(entry.resolution.before_evidence_sha256),
                          after: d(notRecorded(entry.resolution.after_evidence_sha256)),
                          actor: d(entry.resolution.actor_id),
                          role: word("role", entry.resolution.actor_role, locale),
                          reason: d(entry.resolution.reason),
                        }}
                      />
                    </span>
                  ) : null}
                  <div>
                    <Button
                      data-testid={`review-${entry.issue_id}`}
                      onClick={() => {
                        setSelected(entry.issue_id);
                        setDraft({ ...EMPTY_DRAFT, action: allowedActions(entry)[0] ?? "exclude" });
                        setRecorded(null);
                        setError(null);
                      }}
                    >
                      <Tpl
                        k="reviewButton"
                        locale={locale}
                        vars={{ id: d(entry.issue_id.slice(0, 8)) }}
                      />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {loaded.next !== null ? (
            <div>
              <Button
                onClick={() => {
                  armRefocus();
                  void loadMore();
                }}
                disabled={loading || submitting}
              >
                {t("loadMoreButton")}
              </Button>
            </div>
          ) : null}
          {issue ? (
            <form
              aria-label={t("formLabel")}
              onSubmit={(event) => {
                event.preventDefault();
                void handleSubmit();
              }}
              style={{ display: "grid", gap: "var(--space-3)", minInlineSize: 0 }}
            >
              <p data-testid="selected-issue" style={WRAP}>
                <Tpl
                  k="selectedIssue"
                  locale={locale}
                  vars={{
                    id: d(issue.issue_id),
                    severity: word("severity", issue.severity, locale),
                    sha: d(issue.evidence_sha256),
                  }}
                />
              </p>
              {undecidable ? (
                <p role="note" style={WRAP}>
                  {undecidable}
                </p>
              ) : (
                <div style={{ display: "grid", gap: "var(--space-3)", minInlineSize: 0 }}>
                  <div>
                    <label htmlFor={actionId} style={BLOCK_LABEL}>
                      {t("actionLabel")}
                    </label>
                    <select
                      id={actionId}
                      data-testid="resolution-action-select"
                      value={draft.action}
                      style={CONTROL_STYLE}
                      onChange={(event) =>
                        setDraft({ ...draft, action: event.target.value as IssueAction })
                      }
                    >
                      {allowedActions(issue).map((action) => (
                        <option key={action} value={action}>
                          {word("action", action, locale)}
                        </option>
                      ))}
                    </select>
                  </div>
                  {draft.action === "exclude" ? (
                    <fieldset style={{ ...WRAP, margin: 0 }}>
                      <legend>{t("positionsLegend")}</legend>
                      {issue.batch_positions.map((position) => (
                        <label
                          key={position}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "var(--space-2)",
                            minBlockSize: "var(--target-min)",
                          }}
                        >
                          <input
                            type="checkbox"
                            data-testid={`position-${position}`}
                            checked={draft.positions.includes(position)}
                            onChange={() => togglePosition(position)}
                            style={{
                              inlineSize: "var(--target-min)",
                              blockSize: "var(--target-min)",
                            }}
                          />
                          {t("positionLabel", { n: position })}
                        </label>
                      ))}
                    </fieldset>
                  ) : null}
                  {needsSuccessor ? (
                    <Field
                      id={`${actionId}-successor`}
                      label={t("successorLabel")}
                      helper={t("successorHelper")}
                      requirement="required"
                      requirementPlacement="hint"
                      locale={locale}
                      type="text"
                      dir="ltr"
                      value={draft.successor}
                      onChange={(event) => setDraft({ ...draft, successor: event.target.value })}
                      aria-describedby={problems.length > 0 ? problemsId : undefined}
                      style={CONTROL_STYLE}
                    />
                  ) : null}
                  <div>
                    <label htmlFor={rationaleId} style={BLOCK_LABEL}>
                      {t("rationaleLabel")}
                    </label>
                    <p id={`${rationaleId}-hint`} style={{ margin: 0 }}>
                      {t("required")} {t("rationaleHint", { max: REASON_MAX })}
                    </p>
                    <textarea
                      id={rationaleId}
                      data-testid="resolution-rationale"
                      value={draft.reason}
                      aria-required="true"
                      aria-invalid={rationaleProblem ? true : undefined}
                      aria-describedby={`${rationaleId}-hint${problems.length > 0 ? ` ${problemsId}` : ""}`}
                      style={{ ...CONTROL_STYLE, minBlockSize: "6em" }}
                      onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
                    />
                  </div>
                  {problems.length > 0 ? (
                    <ul id={problemsId} aria-label={t("problemsLabel")} style={WRAP}>
                      {problems.map((problem) => (
                        <li key={problem}>{problem}</li>
                      ))}
                    </ul>
                  ) : null}
                  <div>
                    <Button
                      type="submit"
                      variant="primary"
                      data-testid="resolution-submit"
                      disabled={!canSubmit}
                    >
                      {submitting ? t("submittingButton") : t("submitButton")}
                    </Button>
                  </div>
                </div>
              )}
            </form>
          ) : null}
          <h3 style={WRAP}>{t("recentHeading")}</h3>
          {loaded.resolutions.length === 0 ? <p>{t("emptyDecisions")}</p> : null}
          <ul
            aria-label={t("recentList")}
            style={{
              display: "grid",
              gap: "var(--space-2)",
              margin: 0,
              padding: 0,
              listStyle: "none",
            }}
          >
            {loaded.resolutions.map((item) => (
              <li key={item.id} style={WRAP}>
                <Tpl
                  k="recentLine"
                  locale={locale}
                  vars={{
                    action: word("action", item.action, locale),
                    engine: word("state", item.engine_decision, locale),
                    issue: d(item.issue_id),
                    before: d(item.before_evidence_sha256),
                    after: d(notRecorded(item.after_evidence_sha256)),
                    actor: d(item.actor_id),
                    role: word("role", item.actor_role, locale),
                    at: d(item.created_at),
                    reason: d(item.reason),
                  }}
                />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
