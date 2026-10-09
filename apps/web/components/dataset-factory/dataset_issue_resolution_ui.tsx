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

import { useEffect, useRef, useState } from "react";

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
}

const REASON_MAX = 2000;
const PAGE_SIZE = 50;
const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
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

const ERROR_MESSAGES: Record<string, string> = {
  not_found: "The dataset or issue was not found.",
  quality_report_not_found: "No quality report has been recorded for this dataset yet.",
  invalid_body: "The server rejected the decision as malformed.",
  positions_not_in_issue: "The selected positions are not part of this issue.",
  waive_not_permitted: "Your role may not waive an issue.",
  report_version_conflict:
    "The decisions changed since this screen was loaded. Reload and review again.",
  report_superseded: "A newer quality report exists. Reload and review again.",
  issue_already_decided: "This issue already has a decision.",
  waiver_not_allowed_for_blocking: "A blocking issue cannot be waived.",
  detector_failure_requires_rerun: "A detector failure requires a rerun, not a decision.",
  evidence_mismatch: "The issue evidence changed since this screen was loaded. Reload.",
  successor_not_revalidated: "The successor dataset has not been revalidated.",
  successor_not_later_version: "The successor must be a later version of this dataset.",
  idempotency_key_reused: "That request key was already used for a different decision.",
  request_too_large: "The request was too large.",
};

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") {
      return ERROR_MESSAGES[body.detail] ?? body.detail;
    }
  } catch {
    // fall through to the generic message
  }
  return `request failed (${response.status})`;
}

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "not recorded";
  return String(value);
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
function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
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
    isStr(body.actor_id) &&
    body.actor_id.trim() !== "" &&
    isStr(body.actor_role) &&
    ROLES.includes(body.actor_role) &&
    typeof body.resolution_version_after === "number" &&
    Number.isInteger(body.resolution_version_after) &&
    body.resolution_version_after >= 1 &&
    isStr(body.created_at);
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
    isStr(body.created_at) &&
    isStr(body.updated_at);
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
    !Number.isInteger(body.resolution_version) ||
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
export function undecidableReason(issue: DatasetQualityIssue): string | null {
  if (issue.state !== "open") return `Already ${issue.state}; a decision cannot be changed here.`;
  if (issue.is_detector_failure) return "A detector failure requires a rerun, not a decision.";
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

export function DatasetIssueResolutionUI({
  apiBaseUrl,
  organizationId,
  projectId,
  datasetId,
}: DatasetIssueResolutionUIProps) {
  const identity = JSON.stringify([apiBaseUrl, organizationId, projectId, datasetId]);
  const base = datasetBase(apiBaseUrl, organizationId, projectId, datasetId);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [recorded, setRecorded] = useState<DatasetIssueResolution | null>(null);
  const latestRequest = useRef(0);
  const attempt = useRef<{ body: string; key: string } | null>(null);

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
  ): Promise<{ ok: true; body: unknown } | { ok: false; message: string }> {
    const response = await fetch(url, { method: "GET", credentials: "include" });
    if (!response.ok) return { ok: false, message: await readErrorDetail(response) };
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
          setError(reply.message);
          return false;
        }
      }
      if (!reportReply.ok || !issuesReply.ok || !resolutionsReply.ok) return false;
      const report = parseReport(reportReply.body);
      const page = parseIssuePage(issuesReply.body);
      const resolutions = parseResolutionPage(resolutionsReply.body);
      if (report === null || page === null || resolutions === null) {
        setLoaded(null);
        setError("malformed quality response");
        return false;
      }
      if (
        report.dataset_id !== datasetId ||
        page.items.some((issue) => issue.report_id !== report.id)
      ) {
        setLoaded(null);
        setError("quality response does not match the request");
        return false;
      }
      setLoaded({ report, issues: page.items, next: page.next, resolutions });
      return true;
    } catch (caught) {
      if (!stale()) {
        setLoaded(null);
        setError(networkErrorMessage(caught));
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
        setError(reply.message);
        return;
      }
      const page = parseIssuePage(reply.body);
      if (page === null) {
        setError("malformed quality response");
        return;
      }
      if (page.items.some((issue) => issue.report_id !== loaded.report.id)) {
        setError("quality response does not match the request");
        return;
      }
      setLoaded({ ...loaded, issues: [...loaded.issues, ...page.items], next: page.next });
    } catch (caught) {
      if (!stale()) setError(networkErrorMessage(caught));
    } finally {
      if (!stale()) setLoading(false);
    }
  }

  const issue =
    loaded && selected ? (loaded.issues.find((i) => i.issue_id === selected) ?? null) : null;
  const reason = validReason(draft.reason);
  const successorOk = isUuid(draft.successor.trim());
  const needsSuccessor = draft.action === "correct" || draft.action === "new_version";
  const problems: string[] = [];
  if (issue) {
    if (!allowedActions(issue).includes(draft.action))
      problems.push("This action is not available for this issue.");
    if (reason === null) {
      problems.push(
        draft.reason.trim() === ""
          ? "A rationale is required."
          : `The rationale must be at most ${REASON_MAX} characters.`,
      );
    }
    if (draft.action === "exclude" && draft.positions.length === 0) {
      problems.push("Select at least one position to exclude.");
    }
    if (needsSuccessor && !successorOk) problems.push("A successor dataset id (UUID) is required.");
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
    if (needsSuccessor) item.successor_dataset_id = draft.successor.trim();
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
      successor: needsSuccessor ? draft.successor.trim() : null,
    };
    // The same key only for a retry of an identical request that did not conclude.
    if (attempt.current === null || attempt.current.body !== bodyText) {
      attempt.current = { body: bodyText, key: newKey() };
    }
    const key = attempt.current.key;
    const request = ++latestRequest.current;
    const stale = () => request !== latestRequest.current;
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
        const message = await readErrorDetail(response);
        if (!stale()) {
          if (/idempotency_key_reused|already used/.test(message)) attempt.current = null;
          setError(message);
        }
        return;
      }
      const parsed = parseResolveResponse(await response.json());
      if (stale()) return;
      const decision = parsed?.items[0] ?? null;
      if (
        parsed === null ||
        parsed.quality_report_id !== report.id ||
        parsed.resolution_version !== expected.expectedVersion + 1 ||
        parsed.items.length !== 1 ||
        decision === null ||
        !decisionMatchesRequest(decision, expected)
      ) {
        setError("resolution response does not match the request");
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
      if (!stale()) setError(networkErrorMessage(caught));
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

  return (
    <section aria-label="Dataset issue resolution">
      <h2>Dataset quality issues</h2>
      <button type="button" onClick={() => void load()} disabled={loading || submitting}>
        {loading ? "Loading..." : "Load quality report"}
      </button>
      {error ? <p role="alert">{error}</p> : null}
      {recorded ? (
        <div role="status" data-testid="recorded-decision">
          <p>{`Decision recorded: ${recorded.action} (${recorded.engine_decision}) by ${recorded.actor_id} as ${recorded.actor_role}.`}</p>
          <p>{`Before evidence ${recorded.before_evidence_sha256}; after evidence ${shown(recorded.after_evidence_sha256)}.`}</p>
        </div>
      ) : null}
      {loaded ? (
        <div>
          <dl data-testid="report">
            <dt>Dataset version</dt>
            <dd>{loaded.report.dataset_version}</dd>
            <dt>Report</dt>
            <dd>{`#${loaded.report.report_seq} ${loaded.report.report_sha256}`}</dd>
            <dt>Issues</dt>
            <dd>{`${loaded.report.issue_count} (${loaded.report.blocking_count} blocking, ${loaded.report.warning_count} warning)`}</dd>
            <dt>Blocks split</dt>
            <dd>{loaded.report.blocks_split ? "yes" : "no"}</dd>
            <dt>Blocks training</dt>
            <dd>{loaded.report.blocks_training ? "yes" : "no"}</dd>
            <dt>Resolution version</dt>
            <dd>{loaded.report.resolution_version}</dd>
          </dl>
          <p data-testid="residual-risk">{loaded.report.residual_risk_disclosure}</p>
          <ul aria-label="Issues">
            {loaded.issues.map((entry) => (
              <li key={entry.issue_id} data-testid={`issue-${entry.issue_id}`}>
                <span>{`${entry.severity} ${entry.detector}/${entry.category}: ${entry.reason_code} [${entry.state}]`}</span>
                <span>{` positions ${entry.batch_positions.join(", ") || "none"}`}</span>
                {entry.resolution ? (
                  <span data-testid={`evidence-${entry.issue_id}`}>
                    {` before ${entry.resolution.before_evidence_sha256} after ${shown(entry.resolution.after_evidence_sha256)} by ${entry.resolution.actor_id} (${entry.resolution.actor_role}): ${entry.resolution.reason}`}
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => {
                    setSelected(entry.issue_id);
                    setDraft({ ...EMPTY_DRAFT, action: allowedActions(entry)[0] ?? "exclude" });
                    setRecorded(null);
                    setError(null);
                  }}
                >
                  {`Review ${entry.issue_id.slice(0, 8)}`}
                </button>
              </li>
            ))}
          </ul>
          {loaded.next !== null ? (
            <button type="button" onClick={() => void loadMore()} disabled={loading || submitting}>
              Load more issues
            </button>
          ) : null}
          {issue ? (
            <form
              aria-label="Resolve issue"
              onSubmit={(event) => {
                event.preventDefault();
                void handleSubmit();
              }}
            >
              <p data-testid="selected-issue">{`Issue ${issue.issue_id} (${issue.severity}, evidence ${issue.evidence_sha256})`}</p>
              {undecidableReason(issue) ? (
                <p role="note">{undecidableReason(issue)}</p>
              ) : (
                <div>
                  <label>
                    Action
                    <select
                      value={draft.action}
                      onChange={(event) =>
                        setDraft({ ...draft, action: event.target.value as IssueAction })
                      }
                    >
                      {allowedActions(issue).map((action) => (
                        <option key={action} value={action}>
                          {action}
                        </option>
                      ))}
                    </select>
                  </label>
                  {draft.action === "exclude" ? (
                    <fieldset>
                      <legend>Positions to exclude</legend>
                      {issue.batch_positions.map((position) => (
                        <label key={position}>
                          <input
                            type="checkbox"
                            checked={draft.positions.includes(position)}
                            onChange={() => togglePosition(position)}
                          />
                          {`position ${position}`}
                        </label>
                      ))}
                    </fieldset>
                  ) : null}
                  {needsSuccessor ? (
                    <label>
                      Successor dataset id
                      <input
                        type="text"
                        value={draft.successor}
                        onChange={(event) => setDraft({ ...draft, successor: event.target.value })}
                      />
                    </label>
                  ) : null}
                  <label>
                    Rationale
                    <textarea
                      value={draft.reason}
                      onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
                    />
                  </label>
                  {problems.length > 0 ? (
                    <ul aria-label="Why this cannot be submitted">
                      {problems.map((problem) => (
                        <li key={problem}>{problem}</li>
                      ))}
                    </ul>
                  ) : null}
                  <button type="submit" disabled={!canSubmit}>
                    {submitting ? "Submitting..." : "Record decision"}
                  </button>
                </div>
              )}
            </form>
          ) : null}
          <h3>Recent decisions</h3>
          <ul aria-label="Recent decisions">
            {loaded.resolutions.map((item) => (
              <li key={item.id}>
                {`${item.action} (${item.engine_decision}) issue ${item.issue_id} before ${item.before_evidence_sha256} after ${shown(item.after_evidence_sha256)} by ${item.actor_id} (${item.actor_role}) at ${item.created_at}: ${item.reason}`}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
