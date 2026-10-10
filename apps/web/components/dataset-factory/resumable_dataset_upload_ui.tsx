/**
 * Resumable dataset upload UI (FTF-0501-UI, child slice of FTF-0501;
 * Section 8.1, 67.1 "Multipart resumable upload", 72.2).
 *
 * Consumes only the five frozen FTF-0501 routes
 * (`apps/api/app/datasets/direct_object_storage_upload_routes.py`,
 * `build_direct_upload_router`):
 *
 *   POST .../projects/{project_id}/upload-sessions                  create
 *   GET  .../projects/{project_id}/upload-sessions/{id}              read
 *   GET  .../projects/{project_id}/upload-sessions/{id}/parts        list parts (resume)
 *   POST .../projects/{project_id}/upload-sessions/{id}/part-urls    presigned PUT URLs
 *   POST .../projects/{project_id}/upload-sessions/{id}/finalize     verify + create Dataset
 *
 * Acceptance ("Use direct short-lived private multipart upload with
 * finalize byte/hash/type checks and resumable UI", Section 48/503):
 *
 * - A real file picker: dataset purpose and declared content type are an
 *   explicit choice (the server's own closed vocabularies), never inferred
 *   from the file name.
 * - Per-part progress, driven by the real part plan the server returns
 *   (`part_size_bytes`/`part_count` on the session) -- this file never
 *   re-derives that layout. A part counts as uploaded only after a 2xx PUT
 *   of its bytes (or after the real `GET .../parts` listing says so).
 * - Pause/resume after a page reload or a network drop: the *only* source
 *   of truth for what is already uploaded is the real `GET .../parts`
 *   listing. A reload resumes from a small, non-sensitive draft record
 *   (organization, project, connection, session id + declared fields +
 *   file name) kept in `localStorage`; resuming always re-lists parts
 *   first and only ever requests URLs for the part numbers that listing
 *   does not already show -- never a blanket re-upload of every part.
 *
 * Round 2 (class-closing, UI CHECKLIST):
 *
 * (a) Every reply is parsed strictly: a wrong JSON type, a value outside an
 *     enum or a missing required field makes the reply "malformed" -- a
 *     visible state with no upload, resume, re-check or finalize control.
 * (b) Every reply is bound to what was sent: the create reply to the
 *     declared fields and the project/connection props; a resumed session
 *     to the draft; the parts listing to the session's part plan; each
 *     presigned grant to the part requested (PUT, content length = slice
 *     size, one storage origin); the finalize Dataset to the session and
 *     the name sent. A field that differs is malformed, never an upload
 *     into -- or a "Dataset created." for -- something never asked for.
 * (c) Identity: the draft key and the draft itself carry organization,
 *     project and connection; the inner component is keyed on all four
 *     props, so any prop change discards in-flight state, and a stale
 *     async continuation stops at its next await.
 * (d) No simulated progress or success: "Dataset created." appears only
 *     for a 201 whose Dataset body is fully bound; a thrown fetch, a 2xx
 *     with the wrong status or an unbound body is never success.
 * (e) Typed errors (Law 17): 5xx, 401/403, non-JSON bodies and unknown
 *     codes are retryable and keep the draft; only the server's own known
 *     terminal codes (session closed, rejected, expired) clear it.
 *     `dataset_version_conflict` keeps the session resumable; resuming a
 *     verified session shows a visible terminal state.
 *
 * Negative acceptance / Section 72.2 ("secrets/datasets never go to
 * localStorage"; presigned URLs "held in memory only"):
 *
 * - Presigned part URLs are state local to one upload run and are never
 *   written to `localStorage`, not even transiently.
 * - The file's bytes are never written to `localStorage`; resuming after a
 *   reload requires the user to re-pick the file, and the freshly computed
 *   SHA-256 of that reselected file must equal the draft's own declared
 *   value before any part is re-requested.
 *
 * Scope disclosed, not hidden (same precedent as `model_catalog_ui.tsx`,
 * `model_to_artifact_mapping_ui.tsx`, `capability_compatibility_ui.tsx`):
 * this repository has no Next.js `app/` page tree yet (FTF-H029), so this
 * component is UNWIRED (Law 6/7). Proof is a DOM-level Vitest spec plus a
 * real-API spec that runs this component against the real router on
 * PostgreSQL 16 and SeaweedFS in CI; the browser path from `/` waits on
 * FTF-H029.
 */

import { useEffect, useRef, useState } from "react";

// Redeclared from the server's own closed vocabularies (no shared TS/Python
// contract package for domain enums exists yet -- see this file's docstring).
export const ALLOWED_PURPOSES = [
  "training",
  "validation",
  "held_out_evaluation",
  "candidate_development",
  "baseline_only",
] as const;
export type UploadPurpose = (typeof ALLOWED_PURPOSES)[number];

export const SUPPORTED_CONTENT_TYPES = [
  "application/json",
  "application/jsonl",
  "application/x-ndjson",
  "text/csv",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
] as const;
export type UploadContentType = (typeof SUPPORTED_CONTENT_TYPES)[number];

export const SESSION_STATUSES = ["open", "verified", "rejected", "expired"] as const;
export type UploadSessionStatus = (typeof SESSION_STATUSES)[number];

export interface UploadSession {
  id: string;
  project_id: string;
  dataset_source_connection_id: string;
  purpose: UploadPurpose;
  status: UploadSessionStatus;
  declared_size_bytes: number;
  declared_sha256: string;
  declared_content_type: UploadContentType;
  rejection_reason: string | null;
  created_at: string;
  expires_at: string;
  finalized_at: string | null;
  part_size_bytes: number;
  part_count: number;
}

export interface UploadedPart {
  part_number: number;
  size_bytes: number;
}

export interface PartsListing {
  part_size_bytes: number;
  part_count: number;
  parts: UploadedPart[];
}

export interface PartUrlGrant {
  part_number: number;
  method: "PUT";
  url: string;
  content_length: number;
  expires_at: string;
}

export interface UploadedDataset {
  id: string;
  organization_id: string;
  project_id: string;
  name: string;
  version: number;
  purpose: UploadPurpose;
  upload_session_id: string;
  verified_sha256: string;
  verified_size_bytes: number;
  created_by: string;
  created_at: string;
}

interface Draft {
  organizationId: string;
  projectId: string;
  datasetSourceConnectionId: string;
  uploadSessionId: string;
  purpose: UploadPurpose;
  declaredSizeBytes: number;
  declaredSha256: string;
  declaredContentType: UploadContentType;
  fileName: string;
}

export interface ResumableDatasetUploadUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  datasetSourceConnectionId: string;
  /** Optional pin of the object-storage origin presigned URLs must target
   * (for example `https://s3.example.com`). When set, a grant for any other
   * origin is malformed. Independently of this prop, every grant of one
   * upload must share a single origin. */
  storageOrigin?: string;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function sessionsUrl(apiBaseUrl: string, organizationId: string, projectId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/projects/${projectId}/upload-sessions`;
}

export function sessionUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  uploadSessionId: string,
): string {
  return `${sessionsUrl(apiBaseUrl, organizationId, projectId)}/${uploadSessionId}`;
}

export function partsUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  uploadSessionId: string,
): string {
  return `${sessionUrl(apiBaseUrl, organizationId, projectId, uploadSessionId)}/parts`;
}

export function partUrlsUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  uploadSessionId: string,
): string {
  return `${sessionUrl(apiBaseUrl, organizationId, projectId, uploadSessionId)}/part-urls`;
}

export function finalizeUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  uploadSessionId: string,
): string {
  return `${sessionUrl(apiBaseUrl, organizationId, projectId, uploadSessionId)}/finalize`;
}

// ---------------------------------------------------------------------------
// Strict parsing and binding (UI CHECKLIST a, b)
// ---------------------------------------------------------------------------

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

function isPositiveInt(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isTimestamp(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && !Number.isNaN(Date.parse(value));
}

function oneOf<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

function sameId(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

/** A session reply, or null when any field has the wrong type, is outside
 * its enum, is missing, or the part plan is not self-consistent. */
export function parseUploadSession(body: unknown): UploadSession | null {
  if (!isRecord(body)) return null;
  if (!isUuid(body.id)) return null;
  if (!isUuid(body.project_id)) return null;
  if (!isUuid(body.dataset_source_connection_id)) return null;
  if (!oneOf(ALLOWED_PURPOSES, body.purpose)) return null;
  if (!oneOf(SESSION_STATUSES, body.status)) return null;
  if (!isPositiveInt(body.declared_size_bytes)) return null;
  if (typeof body.declared_sha256 !== "string" || !SHA256_PATTERN.test(body.declared_sha256)) {
    return null;
  }
  if (!oneOf(SUPPORTED_CONTENT_TYPES, body.declared_content_type)) return null;
  if (body.rejection_reason !== null && typeof body.rejection_reason !== "string") return null;
  if (!isTimestamp(body.created_at)) return null;
  if (!isTimestamp(body.expires_at)) return null;
  if (body.finalized_at !== null && !isTimestamp(body.finalized_at)) return null;
  if (!isPositiveInt(body.part_size_bytes)) return null;
  if (!isPositiveInt(body.part_count)) return null;
  if (body.part_count !== Math.ceil(body.declared_size_bytes / body.part_size_bytes)) return null;
  return {
    id: body.id,
    project_id: body.project_id,
    dataset_source_connection_id: body.dataset_source_connection_id,
    purpose: body.purpose,
    status: body.status,
    declared_size_bytes: body.declared_size_bytes,
    declared_sha256: body.declared_sha256,
    declared_content_type: body.declared_content_type,
    rejection_reason: body.rejection_reason,
    created_at: body.created_at,
    expires_at: body.expires_at,
    finalized_at: body.finalized_at,
    part_size_bytes: body.part_size_bytes,
    part_count: body.part_count,
  };
}

export interface SessionExpectation {
  id?: string;
  projectId: string;
  datasetSourceConnectionId: string;
  purpose: string;
  declaredSizeBytes: number;
  declaredSha256: string;
  declaredContentType: string;
  status?: UploadSessionStatus;
}

/** The ONE binding function for a session reply: every field the request (or
 * the draft) carried must come back unchanged. */
export function sessionMatches(session: UploadSession, expected: SessionExpectation): boolean {
  return (
    (expected.id === undefined || sameId(session.id, expected.id)) &&
    sameId(session.project_id, expected.projectId) &&
    sameId(session.dataset_source_connection_id, expected.datasetSourceConnectionId) &&
    session.purpose === expected.purpose &&
    session.declared_size_bytes === expected.declaredSizeBytes &&
    session.declared_sha256 === expected.declaredSha256 &&
    session.declared_content_type === expected.declaredContentType &&
    (expected.status === undefined || session.status === expected.status)
  );
}

/** The exact byte range (half-open) of one part, from the session's own
 * real `part_size_bytes`/`part_count`/`declared_size_bytes` -- the last
 * part holds the remainder, same as the server's `plan_parts`. This file
 * never invents its own part-size default. */
export function partByteRange(
  partNumber: number,
  partSizeBytes: number,
  partCount: number,
  declaredSizeBytes: number,
): { start: number; end: number } {
  const start = (partNumber - 1) * partSizeBytes;
  const end = partNumber === partCount ? declaredSizeBytes : start + partSizeBytes;
  return { start, end };
}

function expectedPartSize(session: UploadSession, partNumber: number): number {
  const { start, end } = partByteRange(
    partNumber,
    session.part_size_bytes,
    session.part_count,
    session.declared_size_bytes,
  );
  return end - start;
}

/** A parts listing, or null when malformed or not bound to the session's own
 * part plan (plan, part numbers within 1..part_count, unique, each part's
 * size equal to the plan's size for that part). */
export function parsePartsListing(body: unknown, session: UploadSession): UploadedPart[] | null {
  if (!isRecord(body)) return null;
  if (body.part_size_bytes !== session.part_size_bytes) return null;
  if (body.part_count !== session.part_count) return null;
  if (!Array.isArray(body.parts)) return null;
  const seen = new Set<number>();
  const parts: UploadedPart[] = [];
  for (const item of body.parts as unknown[]) {
    if (!isRecord(item)) return null;
    if (!isPositiveInt(item.part_number) || item.part_number > session.part_count) return null;
    if (!isPositiveInt(item.size_bytes)) return null;
    if (seen.has(item.part_number)) return null;
    if (item.size_bytes !== expectedPartSize(session, item.part_number)) return null;
    seen.add(item.part_number);
    parts.push({ part_number: item.part_number, size_bytes: item.size_bytes });
  }
  return parts;
}

/** The grants for exactly the part numbers requested, or null. Every grant
 * must be a PUT to an absolute http(s) URL without embedded credentials,
 * share one origin with the others (and equal `pinnedOrigin` when set), and
 * carry a content length equal to its slice. */
export function parsePartUrlGrants(
  body: unknown,
  requested: number[],
  session: UploadSession,
  pinnedOrigin: string | null,
): { grants: PartUrlGrant[]; origin: string } | null {
  if (!isRecord(body) || !Array.isArray(body.parts)) return null;
  if (body.parts.length !== requested.length || requested.length === 0) return null;
  const wanted = new Set(requested);
  const seen = new Set<number>();
  const grants: PartUrlGrant[] = [];
  let origin: string | null = pinnedOrigin;
  for (const item of body.parts as unknown[]) {
    if (!isRecord(item)) return null;
    if (!isPositiveInt(item.part_number) || !wanted.has(item.part_number)) return null;
    if (seen.has(item.part_number)) return null;
    if (item.method !== "PUT") return null;
    if (typeof item.url !== "string") return null;
    if (item.content_length !== expectedPartSize(session, item.part_number)) return null;
    if (!isTimestamp(item.expires_at)) return null;
    let parsed: URL;
    try {
      parsed = new URL(item.url);
    } catch {
      return null;
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    if (parsed.username !== "" || parsed.password !== "") return null;
    if (origin === null) origin = parsed.origin;
    if (parsed.origin !== origin) return null;
    seen.add(item.part_number);
    grants.push({
      part_number: item.part_number,
      method: "PUT",
      url: item.url,
      content_length: item.content_length,
      expires_at: item.expires_at,
    });
  }
  if (origin === null) return null;
  return { grants, origin };
}

export interface DatasetExpectation {
  organizationId: string;
  session: UploadSession;
  datasetName: string;
}

/** The finalize Dataset, or null when any field has the wrong type or is
 * missing, or when it is not bound to this organization, project, session
 * and the name sent. */
export function parseUploadedDataset(
  body: unknown,
  expected: DatasetExpectation,
): UploadedDataset | null {
  if (!isRecord(body)) return null;
  if (!isUuid(body.id)) return null;
  if (!isUuid(body.organization_id)) return null;
  if (!isUuid(body.project_id)) return null;
  if (!isNonEmptyString(body.name)) return null;
  if (!isPositiveInt(body.version)) return null;
  if (!oneOf(ALLOWED_PURPOSES, body.purpose)) return null;
  if (!isUuid(body.upload_session_id)) return null;
  if (typeof body.verified_sha256 !== "string" || !SHA256_PATTERN.test(body.verified_sha256)) {
    return null;
  }
  if (!isPositiveInt(body.verified_size_bytes)) return null;
  if (!isNonEmptyString(body.created_by)) return null;
  if (!isTimestamp(body.created_at)) return null;
  const { session } = expected;
  if (!sameId(body.organization_id, expected.organizationId)) return null;
  if (!sameId(body.project_id, session.project_id)) return null;
  if (body.name !== expected.datasetName) return null;
  if (body.purpose !== session.purpose) return null;
  if (!sameId(body.upload_session_id, session.id)) return null;
  if (body.verified_sha256 !== session.declared_sha256) return null;
  if (body.verified_size_bytes !== session.declared_size_bytes) return null;
  return {
    id: body.id,
    organization_id: body.organization_id,
    project_id: body.project_id,
    name: body.name,
    version: body.version,
    purpose: body.purpose,
    upload_session_id: body.upload_session_id,
    verified_sha256: body.verified_sha256,
    verified_size_bytes: body.verified_size_bytes,
    created_by: body.created_by,
    created_at: body.created_at,
  };
}

/** Every part number 1..partCount that `uploaded` (from the real `GET
 * .../parts` listing) does not already contain -- the only calculation
 * this file performs to decide what to request next. */
export function missingPartNumbers(partCount: number, uploaded: UploadedPart[]): number[] {
  const have = new Set(uploaded.map((part) => part.part_number));
  const missing: number[] = [];
  for (let n = 1; n <= partCount; n += 1) {
    if (!have.has(n)) missing.push(n);
  }
  return missing;
}

// ---------------------------------------------------------------------------
// Draft (localStorage): organization, project, connection and declared fields
// only. Secrets, part URLs and file bytes never pass through here (72.2).
// ---------------------------------------------------------------------------

export function draftStorageKey(
  organizationId: string,
  projectId: string,
  datasetSourceConnectionId: string,
): string {
  return `sovra.ftf0501.upload-draft.${organizationId}.${projectId}.${datasetSourceConnectionId}`;
}

function loadDraft(
  key: string,
  organizationId: string,
  projectId: string,
  datasetSourceConnectionId: string,
): Draft | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return null;
    if (
      typeof parsed.organizationId !== "string" ||
      typeof parsed.projectId !== "string" ||
      typeof parsed.datasetSourceConnectionId !== "string" ||
      !sameId(parsed.organizationId, organizationId) ||
      !sameId(parsed.projectId, projectId) ||
      !sameId(parsed.datasetSourceConnectionId, datasetSourceConnectionId) ||
      !isUuid(parsed.uploadSessionId) ||
      !oneOf(ALLOWED_PURPOSES, parsed.purpose) ||
      !isPositiveInt(parsed.declaredSizeBytes) ||
      typeof parsed.declaredSha256 !== "string" ||
      !SHA256_PATTERN.test(parsed.declaredSha256) ||
      !oneOf(SUPPORTED_CONTENT_TYPES, parsed.declaredContentType) ||
      typeof parsed.fileName !== "string"
    ) {
      return null;
    }
    return {
      organizationId: parsed.organizationId,
      projectId: parsed.projectId,
      datasetSourceConnectionId: parsed.datasetSourceConnectionId,
      uploadSessionId: parsed.uploadSessionId,
      purpose: parsed.purpose,
      declaredSizeBytes: parsed.declaredSizeBytes,
      declaredSha256: parsed.declaredSha256,
      declaredContentType: parsed.declaredContentType,
      fileName: parsed.fileName,
    };
  } catch {
    return null;
  }
}

function saveDraft(key: string, draft: Draft): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(draft));
  } catch {
    // Best effort only: a storage quota/denial here must not block the
    // real upload, which is driven by the server's own session state.
  }
}

function clearDraft(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Same as above: never block on this.
  }
}

function readBlobAsArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.readAsArrayBuffer(blob);
  });
}

export async function sha256Hex(blob: Blob): Promise<string> {
  const buffer = await readBlobAsArrayBuffer(blob);
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

const MAX_PART_URLS_PER_REQUEST = 100;

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

// ---------------------------------------------------------------------------
// Typed HTTP failures (UI CHECKLIST e, Law 17)
// ---------------------------------------------------------------------------

const ERROR_MESSAGES: Record<string, string> = {
  upload_session_not_open: "This upload session is no longer open.",
  upload_incomplete: "Not every part has been uploaded yet.",
  dataset_version_conflict:
    "A dataset with this name and version already exists; choose another name. The upload is kept.",
  upload_expired: "This upload session expired before it was finalized.",
  size_mismatch: "The uploaded bytes do not match the declared size; nothing was created.",
  sha256_mismatch: "The uploaded bytes do not match the declared SHA-256; nothing was created.",
  content_type_mismatch:
    "The uploaded content does not match the declared type; nothing was created.",
  part_size_mismatch: "One or more uploaded parts have the wrong size; nothing was created.",
  unexpected_part: "An unexpected part was found in storage; nothing was created.",
  unsupported_content_type: "The declared content type is not supported; nothing was created.",
  source_revoked:
    "The dataset source connection no longer grants this purpose; nothing was created.",
  verification_mismatch: "Verification failed; nothing was created.",
  storage_unavailable: "Storage is temporarily unavailable; this upload can be retried.",
  storage_not_configured: "Storage is not configured; this upload can be retried once it is.",
  invalid_body: "The request was rejected as malformed.",
  invalid_part_numbers: "The requested part numbers were rejected.",
  not_found: "The upload session was not found.",
  purpose_forbidden: "Your role may not upload for this purpose.",
  purpose_not_granted: "The dataset source connection does not grant this purpose.",
  not_an_upload_source: "The dataset source connection is not an upload source.",
  unsupported_media_type: "The declared content type is not supported.",
};

// Codes after which the server has closed (or never had) the session: the
// draft no longer points at anything resumable.
const CLOSED_CODES = new Set([
  "upload_session_not_open",
  "not_found",
  "size_mismatch",
  "sha256_mismatch",
  "content_type_mismatch",
  "part_size_mismatch",
  "unexpected_part",
  "unsupported_content_type",
  "source_revoked",
  "verification_mismatch",
]);

type FailureKind =
  | "closed"
  | "expired"
  | "incomplete"
  | "conflict"
  | "storage_unavailable"
  | "refused"
  | "retryable";

interface Failure {
  kind: FailureKind;
  message: string;
}

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") return body.detail;
  } catch {
    // fall through: a non-JSON body carries no typed code
  }
  return null;
}

function describeFailure(status: number, detail: string | null): string {
  return detail !== null && ERROR_MESSAGES[detail] !== undefined
    ? ERROR_MESSAGES[detail]
    : `request failed (${status}); this can be retried`;
}

/** Only the server's own known (status, code) pairs are terminal or typed.
 * Everything else -- 5xx, 401/403/429, a non-JSON body, an unknown code, a
 * known code on an unexpected status -- is retryable and keeps all state. */
export function classifyFailure(status: number, detail: string | null): Failure {
  const message = describeFailure(status, detail);
  if (detail === null) return { kind: "retryable", message };
  if (status === 410 && detail === "upload_expired") return { kind: "expired", message };
  if (status === 503 && (detail === "storage_unavailable" || detail === "storage_not_configured")) {
    return { kind: "storage_unavailable", message };
  }
  if (status === 409 && detail === "upload_incomplete") return { kind: "incomplete", message };
  if (status === 409 && detail === "dataset_version_conflict") return { kind: "conflict", message };
  if (status === 409 && detail === "upload_session_not_open") return { kind: "closed", message };
  if (status === 404 && detail === "not_found") return { kind: "closed", message };
  if (status === 422 && CLOSED_CODES.has(detail)) return { kind: "closed", message };
  if (
    (status === 400 && (detail === "invalid_body" || detail === "invalid_part_numbers")) ||
    (status === 403 && (detail === "purpose_forbidden" || detail === "purpose_not_granted")) ||
    (status === 409 && detail === "not_an_upload_source")
  ) {
    return { kind: "refused", message };
  }
  return { kind: "retryable", message };
}

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

const MALFORMED_MESSAGE =
  "The server's reply could not be verified against what was requested, so it was not used.";

type Phase =
  | "idle"
  | "resumable"
  | "creating"
  | "uploading"
  | "interrupted"
  | "ready_to_finalize"
  | "finalizing"
  | "accepted"
  | "already_verified"
  | "incomplete"
  | "conflict"
  | "finalize_retry"
  | "expired"
  | "rejected"
  | "storage_unavailable"
  | "malformed";

const FINALIZE_PHASES: readonly Phase[] = [
  "ready_to_finalize",
  "incomplete",
  "conflict",
  "finalize_retry",
  "storage_unavailable",
];

export function ResumableDatasetUploadUI(props: ResumableDatasetUploadUIProps) {
  // Identity (UI CHECKLIST c): any change of API base, organization, project
  // or connection remounts the inner component, discarding every draft,
  // session, file and in-flight result of the previous identity.
  return (
    <UploadInner
      key={`${props.apiBaseUrl}|${props.organizationId}|${props.projectId}|${props.datasetSourceConnectionId}|${props.storageOrigin ?? ""}`}
      {...props}
    />
  );
}

function UploadInner({
  apiBaseUrl,
  organizationId,
  projectId,
  datasetSourceConnectionId,
  storageOrigin,
}: ResumableDatasetUploadUIProps) {
  const storageKey = draftStorageKey(organizationId, projectId, datasetSourceConnectionId);

  const [phase, setPhase] = useState<Phase>("idle");
  const [resumableDraft, setResumableDraft] = useState<Draft | null>(null);
  const [purpose, setPurpose] = useState<UploadPurpose>(ALLOWED_PURPOSES[0]);
  const [contentType, setContentType] = useState<UploadContentType>(SUPPORTED_CONTENT_TYPES[0]);
  const [file, setFile] = useState<File | null>(null);
  const [session, setSession] = useState<UploadSession | null>(null);
  const [uploadedParts, setUploadedParts] = useState<number[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [dataset, setDataset] = useState<UploadedDataset | null>(null);
  const [datasetName, setDatasetName] = useState("");

  const aliveRef = useRef(true);
  const busyRef = useRef(false);
  const originRef = useRef<string | null>(storageOrigin ?? null);

  useEffect(() => {
    aliveRef.current = true;
    const draft = loadDraft(storageKey, organizationId, projectId, datasetSourceConnectionId);
    if (draft) {
      setResumableDraft(draft);
      setPhase("resumable");
    }
    return () => {
      aliveRef.current = false;
    };
    // Runs once per identity (the parent keys this component on it).
  }, [storageKey, organizationId, projectId, datasetSourceConnectionId]);

  async function exclusive(run: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      await run();
    } finally {
      busyRef.current = false;
    }
  }

  function resetToIdle() {
    clearDraft(storageKey);
    setResumableDraft(null);
    setSession(null);
    setUploadedParts([]);
    setFile(null);
    setDatasetName("");
    setErrorMessage(null);
    originRef.current = storageOrigin ?? null;
    setPhase("idle");
  }

  function showMalformed() {
    setErrorMessage(MALFORMED_MESSAGE);
    setPhase("malformed");
  }

  /** A failed read or upload step on an existing session. Retryable failures
   * keep the draft and offer Resume; terminal ones clear it. */
  function applyUploadFailure(failure: Failure) {
    setErrorMessage(failure.message);
    if (failure.kind === "expired") {
      clearDraft(storageKey);
      setPhase("expired");
    } else if (failure.kind === "closed") {
      clearDraft(storageKey);
      setPhase("rejected");
    } else {
      setPhase("interrupted");
    }
  }

  async function uploadMissingParts(activeSession: UploadSession, activeFile: File) {
    setPhase("uploading");
    setErrorMessage(null);
    try {
      const partsResponse = await fetch(
        partsUrl(apiBaseUrl, organizationId, projectId, activeSession.id),
        { method: "GET", credentials: "include" },
      );
      if (!aliveRef.current) return;
      if (!partsResponse.ok) {
        const detail = await readDetail(partsResponse);
        if (!aliveRef.current) return;
        applyUploadFailure(classifyFailure(partsResponse.status, detail));
        return;
      }
      const listing =
        partsResponse.status === 200
          ? parsePartsListing(await partsResponse.json().catch(() => null), activeSession)
          : null;
      if (!aliveRef.current) return;
      if (listing === null) {
        showMalformed();
        return;
      }
      const uploaded = new Set(listing.map((part) => part.part_number));
      const missing = missingPartNumbers(activeSession.part_count, listing);
      setUploadedParts([...uploaded]);

      for (const batch of chunk(missing, MAX_PART_URLS_PER_REQUEST)) {
        const grantResponse = await fetch(
          partUrlsUrl(apiBaseUrl, organizationId, projectId, activeSession.id),
          {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ part_numbers: batch }),
          },
        );
        if (!aliveRef.current) return;
        if (!grantResponse.ok) {
          const detail = await readDetail(grantResponse);
          if (!aliveRef.current) return;
          applyUploadFailure(classifyFailure(grantResponse.status, detail));
          return;
        }
        const parsed =
          grantResponse.status === 200
            ? parsePartUrlGrants(
                await grantResponse.json().catch(() => null),
                batch,
                activeSession,
                originRef.current,
              )
            : null;
        if (!aliveRef.current) return;
        if (parsed === null) {
          showMalformed();
          return;
        }
        originRef.current = parsed.origin;
        for (const grant of parsed.grants) {
          const { start, end } = partByteRange(
            grant.part_number,
            activeSession.part_size_bytes,
            activeSession.part_count,
            activeSession.declared_size_bytes,
          );
          const slice = activeFile.slice(start, end);
          // Presigned storage URL: no app credentials ever go here, and
          // the URL itself is used only in memory, never persisted.
          const putResponse = await fetch(grant.url, { method: "PUT", body: slice });
          if (!aliveRef.current) return;
          if (!putResponse.ok) {
            setErrorMessage(`part ${grant.part_number} upload failed (${putResponse.status})`);
            setPhase("interrupted");
            return;
          }
          // Counted only after a 2xx PUT of this part's bytes.
          uploaded.add(grant.part_number);
          setUploadedParts([...uploaded]);
        }
      }
      if (uploaded.size !== activeSession.part_count) {
        setErrorMessage("not every part was uploaded");
        setPhase("interrupted");
        return;
      }
      setPhase("ready_to_finalize");
    } catch (caught) {
      // A thrown fetch is a dropped connection, not a rejection: the
      // session and its draft both stay in place so Resume can continue.
      if (!aliveRef.current) return;
      setErrorMessage(networkErrorMessage(caught));
      setPhase("interrupted");
    }
  }

  async function startUpload() {
    if (!file) {
      setErrorMessage("choose a file first");
      return;
    }
    setPhase("creating");
    setErrorMessage(null);
    try {
      const declaredSha256 = await sha256Hex(file);
      if (!aliveRef.current) return;
      const sent = {
        dataset_source_connection_id: datasetSourceConnectionId,
        purpose,
        declared_size_bytes: file.size,
        declared_sha256: declaredSha256,
        declared_content_type: contentType,
      };
      const response = await fetch(sessionsUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sent),
      });
      if (!aliveRef.current) return;
      if (!response.ok) {
        const detail = await readDetail(response);
        if (!aliveRef.current) return;
        setErrorMessage(classifyFailure(response.status, detail).message);
        setPhase("idle");
        return;
      }
      const created =
        response.status === 201
          ? parseUploadSession(await response.json().catch(() => null))
          : null;
      if (!aliveRef.current) return;
      if (
        created === null ||
        !sessionMatches(created, {
          projectId,
          datasetSourceConnectionId,
          purpose: sent.purpose,
          declaredSizeBytes: sent.declared_size_bytes,
          declaredSha256: sent.declared_sha256,
          declaredContentType: sent.declared_content_type,
          status: "open",
        })
      ) {
        showMalformed();
        return;
      }
      setSession(created);
      saveDraft(storageKey, {
        organizationId,
        projectId,
        datasetSourceConnectionId,
        uploadSessionId: created.id,
        purpose: created.purpose,
        declaredSizeBytes: created.declared_size_bytes,
        declaredSha256: created.declared_sha256,
        declaredContentType: created.declared_content_type,
        fileName: file.name,
      });
      await uploadMissingParts(created, file);
    } catch (caught) {
      if (!aliveRef.current) return;
      setErrorMessage(networkErrorMessage(caught));
      setPhase("idle");
    }
  }

  /** Read the session and route by its server-side status. `pickedFile` is
   * only needed to continue an open session. */
  async function loadSessionFromDraft(
    draft: Draft,
    pickedFile: File | null,
    fallbackPhase: Phase,
  ): Promise<void> {
    const response = await fetch(
      sessionUrl(apiBaseUrl, organizationId, projectId, draft.uploadSessionId),
      { method: "GET", credentials: "include" },
    );
    if (!aliveRef.current) return;
    if (!response.ok) {
      const detail = await readDetail(response);
      if (!aliveRef.current) return;
      const failure = classifyFailure(response.status, detail);
      setErrorMessage(failure.message);
      if (failure.kind === "closed") {
        clearDraft(storageKey);
        setPhase("rejected");
      } else if (failure.kind === "expired") {
        clearDraft(storageKey);
        setPhase("expired");
      } else {
        // Retryable: the draft stays and the user can try again.
        setPhase(fallbackPhase);
      }
      return;
    }
    const current =
      response.status === 200 ? parseUploadSession(await response.json().catch(() => null)) : null;
    if (!aliveRef.current) return;
    if (
      current === null ||
      !sessionMatches(current, {
        id: draft.uploadSessionId,
        projectId: draft.projectId,
        datasetSourceConnectionId: draft.datasetSourceConnectionId,
        purpose: draft.purpose,
        declaredSizeBytes: draft.declaredSizeBytes,
        declaredSha256: draft.declaredSha256,
        declaredContentType: draft.declaredContentType,
      })
    ) {
      showMalformed();
      return;
    }
    if (current.status === "verified") {
      clearDraft(storageKey);
      setErrorMessage(null);
      setPhase("already_verified");
      return;
    }
    if (current.status === "expired") {
      clearDraft(storageKey);
      setErrorMessage(ERROR_MESSAGES.upload_expired ?? null);
      setPhase("expired");
      return;
    }
    if (current.status === "rejected") {
      clearDraft(storageKey);
      const reason = current.rejection_reason;
      setErrorMessage(
        reason !== null ? (ERROR_MESSAGES[reason] ?? `rejected: ${reason}`) : "rejected",
      );
      setPhase("rejected");
      return;
    }
    setSession(current);
    if (pickedFile) {
      setFile(pickedFile);
      setResumableDraft(null);
      await uploadMissingParts(current, pickedFile);
    } else {
      setPhase(fallbackPhase);
    }
  }

  async function resumeFromDraft(pickedFile: File) {
    if (!resumableDraft) return;
    setErrorMessage(null);
    setPhase("creating");
    try {
      const recomputed = await sha256Hex(pickedFile);
      if (!aliveRef.current) return;
      if (
        recomputed !== resumableDraft.declaredSha256 ||
        pickedFile.size !== resumableDraft.declaredSizeBytes
      ) {
        setErrorMessage(
          "the selected file does not match the upload in progress; choose the original file, " +
            "or abandon this draft to start a new upload",
        );
        setPhase("resumable");
        return;
      }
      await loadSessionFromDraft(resumableDraft, pickedFile, "resumable");
    } catch (caught) {
      if (!aliveRef.current) return;
      setErrorMessage(networkErrorMessage(caught));
      setPhase("resumable");
    }
  }

  async function checkStatus() {
    if (!session) return;
    setErrorMessage(null);
    try {
      await loadSessionFromDraft(
        {
          organizationId,
          projectId,
          datasetSourceConnectionId,
          uploadSessionId: session.id,
          purpose: session.purpose,
          declaredSizeBytes: session.declared_size_bytes,
          declaredSha256: session.declared_sha256,
          declaredContentType: session.declared_content_type,
          fileName: file?.name ?? "",
        },
        file,
        phase,
      );
    } catch (caught) {
      if (!aliveRef.current) return;
      setErrorMessage(networkErrorMessage(caught));
      setPhase(phase);
    }
  }

  function leaveMalformed() {
    const draft = loadDraft(storageKey, organizationId, projectId, datasetSourceConnectionId);
    setErrorMessage(null);
    setSession(null);
    setUploadedParts([]);
    setFile(null);
    originRef.current = storageOrigin ?? null;
    if (draft) {
      setResumableDraft(draft);
      setPhase("resumable");
    } else {
      setPhase("idle");
    }
  }

  async function finalize() {
    if (!session || !sameId(session.project_id, projectId)) return;
    const name = datasetName.trim();
    if (!name) return;
    setPhase("finalizing");
    setErrorMessage(null);
    try {
      const response = await fetch(finalizeUrl(apiBaseUrl, organizationId, projectId, session.id), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_name: name }),
      });
      if (!aliveRef.current) return;
      if (response.ok) {
        // "Dataset created." only for a 201 whose Dataset is bound to this
        // organization, project, session and the name sent.
        const bound =
          response.status === 201
            ? parseUploadedDataset(await response.json().catch(() => null), {
                organizationId,
                session,
                datasetName: name,
              })
            : null;
        if (!aliveRef.current) return;
        if (bound === null) {
          // The finalize may or may not have taken effect; keep the draft and
          // let the user re-read the session (read-only) to find out.
          showMalformed();
          return;
        }
        setDataset(bound);
        setPhase("accepted");
        clearDraft(storageKey);
        return;
      }
      const detail = await readDetail(response);
      if (!aliveRef.current) return;
      const failure = classifyFailure(response.status, detail);
      setErrorMessage(failure.message);
      switch (failure.kind) {
        case "incomplete":
          setPhase("incomplete"); // session stays open and resumable; draft is kept.
          return;
        case "conflict":
          setPhase("conflict"); // session stays open; choose another name; draft is kept.
          return;
        case "storage_unavailable":
          setPhase("storage_unavailable"); // session stays open server-side; draft is kept.
          return;
        case "expired":
          clearDraft(storageKey);
          setPhase("expired");
          return;
        case "closed":
          clearDraft(storageKey);
          setPhase("rejected");
          return;
        default:
          // refused (bad name) or retryable (5xx, 401/403, non-JSON, unknown
          // code): nothing is known to have closed -- keep everything.
          setPhase("finalize_retry");
      }
    } catch (caught) {
      if (!aliveRef.current) return;
      setErrorMessage(networkErrorMessage(caught));
      setPhase("finalize_retry");
    }
  }

  const partCount = session?.part_count ?? 0;
  const uploadedCount = uploadedParts.length;
  const allPartsUploaded = partCount > 0 && uploadedCount >= partCount;
  const isFinalizing = phase === "finalizing";

  return (
    <div data-testid="resumable-dataset-upload-ui" aria-label="Resumable dataset upload">
      {phase === "resumable" && resumableDraft && (
        <section data-testid="upload-resume-banner">
          <p data-testid="upload-resume-text">
            A previous upload of &quot;{resumableDraft.fileName}&quot; is still in progress. Choose
            that same file to resume it.
          </p>
          {errorMessage && (
            <p role="alert" data-testid="upload-resume-error">
              {errorMessage}
            </p>
          )}
          <input
            type="file"
            data-testid="upload-resume-file-input"
            onChange={(event) => {
              const picked = event.target.files?.[0];
              if (picked) void exclusive(() => resumeFromDraft(picked));
            }}
          />
          <button type="button" data-testid="upload-abandon-draft-button" onClick={resetToIdle}>
            Abandon this draft and start a new upload
          </button>
        </section>
      )}

      {phase === "idle" && (
        <section data-testid="upload-form">
          {errorMessage && (
            <p role="alert" data-testid="upload-error-text">
              {errorMessage}
            </p>
          )}
          <label>
            Purpose
            <select
              data-testid="upload-purpose-select"
              value={purpose}
              onChange={(event) => setPurpose(event.target.value as UploadPurpose)}
            >
              {ALLOWED_PURPOSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            Content type
            <select
              data-testid="upload-content-type-select"
              value={contentType}
              onChange={(event) => setContentType(event.target.value as UploadContentType)}
            >
              {SUPPORTED_CONTENT_TYPES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <input
            type="file"
            data-testid="upload-file-input"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            data-testid="upload-start-button"
            disabled={!file}
            onClick={() => void exclusive(startUpload)}
          >
            Start upload
          </button>
        </section>
      )}

      {(phase === "creating" || phase === "uploading") && (
        <section data-testid="upload-progress">
          <p data-testid="upload-progress-text">
            Uploaded {uploadedCount} of {partCount || "?"} parts.
          </p>
        </section>
      )}

      {phase === "interrupted" && session && (
        <section data-testid="upload-interrupted">
          <p data-testid="upload-interrupted-text">
            Upload interrupted after {uploadedCount} of {partCount} parts. The session is still
            open; resuming only re-uploads the parts storage does not already have.
          </p>
          {errorMessage && (
            <p role="alert" data-testid="upload-interrupted-reason">
              {errorMessage}
            </p>
          )}
          <button
            type="button"
            data-testid="upload-resume-button"
            onClick={() => file && void exclusive(() => uploadMissingParts(session, file))}
          >
            Resume upload
          </button>
        </section>
      )}

      {FINALIZE_PHASES.includes(phase) ? (
        <section data-testid="upload-finalize">
          {allPartsUploaded && phase !== "incomplete" && (
            <p data-testid="upload-all-parts-uploaded">All parts uploaded.</p>
          )}
          {(!allPartsUploaded || phase === "incomplete") && session && (
            <button
              type="button"
              data-testid="upload-recheck-parts-button"
              onClick={() => file && void exclusive(() => uploadMissingParts(session, file))}
            >
              Re-check uploaded parts and resume
            </button>
          )}
          <label>
            Dataset name
            <input
              type="text"
              data-testid="upload-dataset-name-input"
              value={datasetName}
              onChange={(event) => setDatasetName(event.target.value)}
            />
          </label>
          <button
            type="button"
            data-testid="upload-finalize-button"
            disabled={!datasetName.trim() || isFinalizing}
            onClick={() => void exclusive(finalize)}
          >
            Finalize
          </button>
        </section>
      ) : null}

      {phase === "finalizing" && <p data-testid="upload-finalizing-text">Finalizing...</p>}

      {phase === "accepted" && dataset && (
        <section data-testid="upload-accepted" role="status">
          <p data-testid="upload-accepted-text">Dataset created.</p>
          <p data-testid="upload-accepted-dataset-id">{dataset.id}</p>
        </section>
      )}

      {phase === "already_verified" && (
        <p role="status" data-testid="upload-already-verified-text">
          This upload session was already finalized and verified; its Dataset exists. Nothing more
          to upload.
        </p>
      )}

      {phase === "malformed" && (
        <section data-testid="upload-malformed">
          <p role="alert" data-testid="upload-malformed-text">
            {errorMessage}
          </p>
          {session && (
            <button
              type="button"
              data-testid="upload-check-status-button"
              onClick={() => void exclusive(checkStatus)}
            >
              Check upload status
            </button>
          )}
          <button type="button" data-testid="upload-malformed-back-button" onClick={leaveMalformed}>
            Back
          </button>
        </section>
      )}

      {phase === "incomplete" && (
        <p role="alert" data-testid="upload-incomplete-text">
          {errorMessage}
        </p>
      )}
      {phase === "conflict" && (
        <p role="alert" data-testid="upload-conflict-text">
          {errorMessage}
        </p>
      )}
      {phase === "finalize_retry" && (
        <>
          <p role="alert" data-testid="upload-finalize-retry-text">
            {errorMessage}
          </p>
          <button
            type="button"
            data-testid="upload-check-status-button"
            onClick={() => void exclusive(checkStatus)}
          >
            Check upload status
          </button>
        </>
      )}
      {phase === "expired" && (
        <p role="alert" data-testid="upload-expired-text">
          {errorMessage ?? "This upload session expired."}
        </p>
      )}
      {phase === "rejected" && (
        <p role="alert" data-testid="upload-rejected-text">
          {errorMessage}
        </p>
      )}
      {phase === "storage_unavailable" && (
        <p role="alert" data-testid="upload-storage-unavailable-text">
          {errorMessage}
        </p>
      )}
    </div>
  );
}
