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
 *   explicit choice (the server's own closed vocabularies -- `training`,
 *   `validation`, `held_out_evaluation`, `candidate_development`,
 *   `baseline_only` and the five supported content types -- redeclared
 *   below, same precedent as `dataset_source_selector_purpose_
 *   authorization_ui.tsx`'s own enums), never inferred from the file name.
 * - Per-part progress, driven by the real part plan the server returns
 *   (`part_size_bytes`/`part_count` on the session) -- this file never
 *   re-derives that layout.
 * - Pause/resume after a page reload or a network drop: the *only* source
 *   of truth for what is already uploaded is the real `GET .../parts`
 *   listing. A reload resumes from a small, non-sensitive draft record
 *   (session id + declared fields + file name) kept in `localStorage`
 *   (Section 72.2 "Wizard stores actual selected IDs ... and progress");
 *   resuming always re-lists parts first and only ever requests URLs for
 *   the part numbers that listing does not already show -- never a
 *   blanket re-upload of every part.
 * - The typed finalize outcome (201 accepted, 409 incomplete, 410 expired,
 *   422 with the real rejection reason, 503 storage outage) is shown as a
 *   distinct, visible state. Nothing is ever shown as accepted unless the
 *   route's own 201 with a real Dataset body says so (Law 17); a 503 keeps
 *   the draft (the session stays open server-side) and every other
 *   terminal outcome clears it.
 *
 * Negative acceptance / Section 72.2 ("secrets/datasets never go to
 * localStorage"; presigned URLs "held in memory only"):
 *
 * - Presigned part URLs are state local to one upload run and are never
 *   written to `localStorage`, not even transiently.
 * - The file's bytes are never written to `localStorage`; resuming after a
 *   reload requires the user to re-pick the file, and the freshly computed
 *   SHA-256 of that reselected file must equal the draft's own declared
 *   value before any part is re-requested -- otherwise resuming is refused
 *   with a typed error rather than silently uploading the wrong bytes
 *   under someone else's session id.
 *
 * Scope disclosed, not hidden (same precedent as `model_catalog_ui.tsx`,
 * `model_to_artifact_mapping_ui.tsx`, `capability_compatibility_ui.tsx`):
 * this repository has no Next.js `app/` page tree yet (FTF-H029), so proof
 * here is a real DOM-level Vitest + React Testing Library spec exercising
 * this actual component against a mocked `fetch` (including a thrown
 * network error standing in for a dropped connection), not a production
 * page mount. Law 6 (browser reachability from `/`) stays disclosed
 * BLOCKED on FTF-H029, same as every other UI task in this repository so
 * far; there is no live `/` to run a Playwright journey from yet, and
 * building one is FTF-H029's own scope, not this file's closed list.
 */

import { useEffect, useState } from "react";

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

export interface UploadSession {
  id: string;
  project_id: string;
  dataset_source_connection_id: string;
  purpose: string;
  status: string;
  declared_size_bytes: number;
  declared_sha256: string;
  declared_content_type: string;
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
  method: string;
  url: string;
  content_length: number;
  expires_at: string;
}

interface Draft {
  uploadSessionId: string;
  projectId: string;
  datasetSourceConnectionId: string;
  purpose: string;
  declaredSizeBytes: number;
  declaredSha256: string;
  declaredContentType: string;
  fileName: string;
}

export interface ResumableDatasetUploadUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  datasetSourceConnectionId: string;
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

function draftStorageKey(projectId: string, datasetSourceConnectionId: string): string {
  return `sovra.ftf0501.upload-draft.${projectId}.${datasetSourceConnectionId}`;
}

// Secrets, part URLs and file bytes never pass through here (Section 72.2);
// only the draft shape above (ids, declared fields, file name) ever does.
function loadDraft(key: string): Draft | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Draft>;
    if (
      typeof parsed.uploadSessionId !== "string" ||
      typeof parsed.projectId !== "string" ||
      typeof parsed.datasetSourceConnectionId !== "string" ||
      typeof parsed.purpose !== "string" ||
      typeof parsed.declaredSizeBytes !== "number" ||
      typeof parsed.declaredSha256 !== "string" ||
      typeof parsed.declaredContentType !== "string" ||
      typeof parsed.fileName !== "string"
    ) {
      return null;
    }
    return parsed as Draft;
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

const MAX_PART_URLS_PER_REQUEST = 100;

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

const FINALIZE_ERROR_MESSAGES: Record<string, string> = {
  upload_session_not_open: "This upload session is no longer open.",
  upload_incomplete: "Not every part has been uploaded yet.",
  dataset_version_conflict: "A dataset with this name and version already exists.",
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
  not_found: "The upload session was not found.",
};

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return FINALIZE_ERROR_MESSAGES[body.detail] ?? body.detail;
    }
  } catch {
    // fall through
  }
  return `request failed (${response.status})`;
}

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

type Phase =
  | "idle"
  | "resumable"
  | "creating"
  | "uploading"
  | "interrupted"
  | "ready_to_finalize"
  | "finalizing"
  | "accepted"
  | "incomplete"
  | "expired"
  | "rejected"
  | "storage_unavailable"
  | "error";

export function ResumableDatasetUploadUI({
  apiBaseUrl,
  organizationId,
  projectId,
  datasetSourceConnectionId,
}: ResumableDatasetUploadUIProps) {
  const storageKey = draftStorageKey(projectId, datasetSourceConnectionId);

  const [phase, setPhase] = useState<Phase>("idle");
  const [resumableDraft, setResumableDraft] = useState<Draft | null>(null);
  const [purpose, setPurpose] = useState<UploadPurpose>(ALLOWED_PURPOSES[0]);
  const [contentType, setContentType] = useState<UploadContentType>(SUPPORTED_CONTENT_TYPES[0]);
  const [file, setFile] = useState<File | null>(null);
  const [session, setSession] = useState<UploadSession | null>(null);
  const [uploadedCount, setUploadedCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [dataset, setDataset] = useState<Record<string, unknown> | null>(null);
  const [datasetName, setDatasetName] = useState("");

  useEffect(() => {
    const draft = loadDraft(storageKey);
    if (draft) {
      setResumableDraft(draft);
      setPhase("resumable");
    }
    // Deliberately runs once per mount only, keyed to this draft slot: a
    // reload is the only case this restores from, never a background poll.
  }, [storageKey]);

  function resetToIdle() {
    clearDraft(storageKey);
    setResumableDraft(null);
    setSession(null);
    setUploadedCount(0);
    setFile(null);
    setDatasetName("");
    setErrorMessage(null);
    setPhase("idle");
  }

  async function uploadMissingParts(
    activeSession: UploadSession,
    activeFile: File,
  ): Promise<boolean> {
    setPhase("uploading");
    setErrorMessage(null);
    try {
      const partsResponse = await fetch(
        partsUrl(apiBaseUrl, organizationId, projectId, activeSession.id),
        { method: "GET", credentials: "include" },
      );
      if (!partsResponse.ok) {
        setErrorMessage(await readErrorDetail(partsResponse));
        setPhase("error");
        return false;
      }
      const listing = (await partsResponse.json()) as PartsListing;
      const missing = missingPartNumbers(activeSession.part_count, listing.parts);
      setUploadedCount(activeSession.part_count - missing.length);

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
        if (!grantResponse.ok) {
          setErrorMessage(await readErrorDetail(grantResponse));
          setPhase(grantResponse.status === 410 ? "expired" : "interrupted");
          if (grantResponse.status === 410) clearDraft(storageKey);
          return false;
        }
        const body = (await grantResponse.json()) as { parts: PartUrlGrant[] };
        for (const grant of body.parts) {
          const { start, end } = partByteRange(
            grant.part_number,
            activeSession.part_size_bytes,
            activeSession.part_count,
            activeSession.declared_size_bytes,
          );
          const slice = activeFile.slice(start, end);
          // Presigned storage URL: no app credentials ever go here, and
          // the URL itself is used only in memory, never persisted.
          const putResponse = await fetch(grant.url, { method: grant.method, body: slice });
          if (!putResponse.ok) {
            setErrorMessage(`part ${grant.part_number} upload failed (${putResponse.status})`);
            setPhase("interrupted");
            return false;
          }
          setUploadedCount((count) => count + 1);
        }
      }
      setPhase("ready_to_finalize");
      return true;
    } catch (caught) {
      // A thrown fetch is a dropped connection, not a rejection: the
      // session and its draft both stay in place so Resume can continue.
      setErrorMessage(networkErrorMessage(caught));
      setPhase("interrupted");
      return false;
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
      const response = await fetch(sessionsUrl(apiBaseUrl, organizationId, projectId), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataset_source_connection_id: datasetSourceConnectionId,
          purpose,
          declared_size_bytes: file.size,
          declared_sha256: declaredSha256,
          declared_content_type: contentType,
        }),
      });
      if (!response.ok) {
        setErrorMessage(await readErrorDetail(response));
        setPhase("error");
        return;
      }
      const created = (await response.json()) as UploadSession;
      setSession(created);
      saveDraft(storageKey, {
        uploadSessionId: created.id,
        projectId,
        datasetSourceConnectionId,
        purpose: created.purpose,
        declaredSizeBytes: created.declared_size_bytes,
        declaredSha256: created.declared_sha256,
        declaredContentType: created.declared_content_type,
        fileName: file.name,
      });
      await uploadMissingParts(created, file);
    } catch (caught) {
      setErrorMessage(networkErrorMessage(caught));
      setPhase("error");
    }
  }

  async function resumeFromDraft(pickedFile: File) {
    if (!resumableDraft) return;
    setErrorMessage(null);
    setPhase("creating");
    try {
      const recomputed = await sha256Hex(pickedFile);
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
      const response = await fetch(
        sessionUrl(apiBaseUrl, organizationId, projectId, resumableDraft.uploadSessionId),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setErrorMessage(await readErrorDetail(response));
        setPhase("error");
        clearDraft(storageKey);
        return;
      }
      const current = (await response.json()) as UploadSession;
      if (current.status !== "open") {
        setPhase(
          current.status === "verified"
            ? "accepted"
            : current.status === "expired"
              ? "expired"
              : "rejected",
        );
        setErrorMessage(
          current.status === "verified" ? null : (current.rejection_reason ?? current.status),
        );
        clearDraft(storageKey);
        return;
      }
      setSession(current);
      setFile(pickedFile);
      setResumableDraft(null);
      await uploadMissingParts(current, pickedFile);
    } catch (caught) {
      setErrorMessage(networkErrorMessage(caught));
      setPhase("error");
    }
  }

  async function finalize() {
    if (!session) return;
    setPhase("finalizing");
    setErrorMessage(null);
    try {
      const response = await fetch(finalizeUrl(apiBaseUrl, organizationId, projectId, session.id), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_name: datasetName }),
      });
      const body = await response.json().catch(() => null);
      if (response.ok) {
        setDataset(body as Record<string, unknown>);
        setPhase("accepted");
        clearDraft(storageKey);
        return;
      }
      const detail = body && typeof body.detail === "string" ? body.detail : null;
      setErrorMessage(
        detail
          ? (FINALIZE_ERROR_MESSAGES[detail] ?? detail)
          : `request failed (${response.status})`,
      );
      if (response.status === 409 && detail === "upload_incomplete") {
        setPhase("incomplete");
        return; // session stays open and resumable; draft is kept.
      }
      if (response.status === 410) {
        setPhase("expired");
        clearDraft(storageKey);
        return;
      }
      if (response.status === 503) {
        setPhase("storage_unavailable");
        return; // session stays open server-side; draft is kept.
      }
      // Every other outcome (422 rejection, 409 not-open/version-conflict,
      // 404) closes or invalidates the session server-side: the draft no
      // longer points at anything resumable.
      setPhase("rejected");
      clearDraft(storageKey);
    } catch (caught) {
      setErrorMessage(networkErrorMessage(caught));
      setPhase("interrupted");
    }
  }

  const partCount = session?.part_count ?? 0;
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
          <input
            type="file"
            data-testid="upload-resume-file-input"
            onChange={(event) => {
              const picked = event.target.files?.[0];
              if (picked) void resumeFromDraft(picked);
            }}
          />
          <button type="button" data-testid="upload-abandon-draft-button" onClick={resetToIdle}>
            Abandon this draft and start a new upload
          </button>
        </section>
      )}

      {phase === "idle" && (
        <section data-testid="upload-form">
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
            onClick={() => void startUpload()}
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
          <button
            type="button"
            data-testid="upload-resume-button"
            onClick={() => file && void uploadMissingParts(session, file)}
          >
            Resume upload
          </button>
        </section>
      )}

      {phase === "ready_to_finalize" ||
      phase === "incomplete" ||
      phase === "storage_unavailable" ? (
        <section data-testid="upload-finalize">
          {allPartsUploaded && phase !== "incomplete" && (
            <p data-testid="upload-all-parts-uploaded">All parts uploaded.</p>
          )}
          {(!allPartsUploaded || phase === "incomplete") && session && (
            <button
              type="button"
              data-testid="upload-recheck-parts-button"
              onClick={() => file && void uploadMissingParts(session, file)}
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
            onClick={() => void finalize()}
          >
            Finalize
          </button>
        </section>
      ) : null}

      {phase === "finalizing" && <p data-testid="upload-finalizing-text">Finalizing...</p>}

      {phase === "accepted" && dataset && (
        <section data-testid="upload-accepted" role="status">
          <p data-testid="upload-accepted-text">Dataset created.</p>
          <p data-testid="upload-accepted-dataset-id">{String(dataset.id ?? "")}</p>
        </section>
      )}

      {phase === "incomplete" && (
        <p role="alert" data-testid="upload-incomplete-text">
          {errorMessage}
        </p>
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
      {phase === "error" && (
        <p role="alert" data-testid="upload-error-text">
          {errorMessage}
        </p>
      )}
    </div>
  );
}
