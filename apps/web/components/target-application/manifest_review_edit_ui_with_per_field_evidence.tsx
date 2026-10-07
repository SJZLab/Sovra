/**
 * Manifest review/edit UI with per-field evidence (FTF-04A21, Section
 * 6/7A/66/77/81).
 *
 * First live caller of the already-CERTIFIED `/manifests` (FTF-04A19) and
 * `/manifests/{id}/evidence` (FTF-04A20) routes. This component never
 * decides anything itself: every effect (save, submit for review, lock) is
 * the real backend's own authoritative check; the client-side gating
 * below is defense in depth only, never the enforcement.
 *
 * Acceptance ("Show field evidence/diff/override and persist draft while
 * model/runtime prerequisites resolve"):
 * - Evidence: every content field lists exactly the `ManifestEvidence`
 *   rows the live route returned for that field (leading dot-segment of
 *   `field_path`). A field with no row says so -- evidence is never
 *   invented, and a field the user has overridden is labelled as such.
 * - Diff: an edited field renders a line diff between the server's value
 *   and the user's draft.
 * - Override: each field is an editable JSON (or plain text for
 *   `agent_name`) control while the manifest is `draft`/`review_required`.
 * - Persist draft: "Save draft" PATCHes only the changed fields to the
 *   real route, so the work survives on the server while model/runtime
 *   prerequisites are still being resolved.
 *
 * Negative acceptance ("Page refresh does not lose work; no lock until
 * conflicts and exact current model role are resolved"):
 * - Unsaved edits are mirrored to browser storage on every keystroke,
 *   keyed by organization/project/manifest, and restored on the next
 *   mount. Storage is best-effort (it can throw or be empty); the page
 *   renders without it.
 * - Each restored draft remembers the server text it was based on. If the
 *   server's value has since changed, that field is a CONFLICT the user
 *   must resolve explicitly ("keep mine" rebases the draft on the new
 *   server value, "take server's" drops it) -- never silently merged.
 * - "Lock" stays disabled until: status is `review_required`; no field is
 *   unsaved, invalid JSON or conflicting; the three critical fields the
 *   backend requires are present; and the current model binding names an
 *   explicit, resolved role from Section 0.12's five roles. The role is
 *   chosen by the user (never guessed from a LiteLLM alias) and is stored
 *   as the `role` key of `current_model_binding_json`.
 *
 * Scope disclosed, not hidden (same precedent as the sibling
 * target-application components): this repository has no Next.js `app/`
 * page tree yet (FTF-H029), so this is a DOM-level tested component
 * (Vitest + React Testing Library + jsdom with a mocked `fetch`), not a
 * full browser E2E -- Law 6 stays disclosed BLOCKED on that dependency.
 */

import { useEffect, useMemo, useState } from "react";

// Matches AgentManifest CONTENT_FIELDS minus `target_source_snapshot_id`
// (fixed at creation) -- exactly the fields the live PATCH accepts.
export const TEXT_FIELDS = ["agent_name"] as const;
export const JSON_FIELDS = [
  "agent_entrypoint_json",
  "current_model_binding_json",
  "system_prompt_refs_json",
  "tool_contracts_json",
  "input_contract_json",
  "output_contract_json",
  "mandate_json",
  "forbidden_actions_json",
  "permission_model_json",
  "required_context_json",
  "runtime_dependencies_json",
  "evaluation_harness_json",
  "trace_source_contract_json",
  "deployment_contract_json",
] as const;
export const EDITABLE_FIELDS: readonly string[] = [...TEXT_FIELDS, ...JSON_FIELDS];

// Mirrors agent_manifest_schema.CRITICAL_LOCK_FIELDS.
export const CRITICAL_LOCK_FIELDS: readonly string[] = [
  "agent_entrypoint_json",
  "current_model_binding_json",
  "runtime_dependencies_json",
];
export const MODEL_BINDING_FIELD = "current_model_binding_json";
// Section 0.12's explicitly assigned roles.
export const MODEL_ROLES = ["current_production", "baseline", "candidate", "teacher", "judge"];

export interface ManifestRecord {
  id: string;
  project_id: string;
  status: string;
  version: number;
  manifest_sha256: string | null;
  [field: string]: unknown;
}

export interface EvidenceRecord {
  id: string;
  field_path: string;
  evidence_type: string;
  repository_commit_sha: string | null;
  file_path: string | null;
  line_start: number | null;
  line_end: number | null;
  symbol: string | null;
  content_hash: string | null;
  confidence_score: number | null;
}

export interface ManifestReviewEditUIProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  manifestId: string;
  /** Injectable for tests; defaults to `window.localStorage` when usable. */
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
}

interface StoredDraft {
  text: string;
  baseText: string;
}

type FieldState = "clean" | "unsaved" | "invalid" | "conflict";

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

function manifestBase(apiBaseUrl: string, organizationId: string, projectId: string): string {
  return `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}/projects/${projectId}/manifests`;
}

export function manifestUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  manifestId: string,
): string {
  return `${manifestBase(apiBaseUrl, organizationId, projectId)}/${manifestId}`;
}

export function evidenceUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  manifestId: string,
): string {
  return `${manifestUrl(apiBaseUrl, organizationId, projectId, manifestId)}/evidence`;
}

export function draftStorageKey(
  organizationId: string,
  projectId: string,
  manifestId: string,
): string {
  return `sovra:manifest-draft:${organizationId}:${projectId}:${manifestId}`;
}

/** The text a field shows for the server's current value. */
export function serverText(manifest: ManifestRecord, field: string): string {
  const value = manifest[field];
  if ((TEXT_FIELDS as readonly string[]).includes(field)) {
    return typeof value === "string" ? value : "";
  }
  return JSON.stringify(value ?? null, null, 2);
}

/** Parses a JSON-field draft; `ok: false` is an invalid-JSON conflict. */
export function parseJsonDraft(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

export interface DiffLine {
  kind: "same" | "removed" | "added";
  text: string;
}

/** Line diff (longest-common-subsequence) between server and draft text. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  // lcs[i][j] = LCS length of a[i..] and b[j..]; rows are flat arrays so
  // every read goes through `cell`, which treats out-of-range as 0.
  const width = b.length + 1;
  const lcs = new Array<number>((a.length + 1) * width).fill(0);
  const cell = (i: number, j: number): number => lcs[i * width + j] ?? 0;
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i * width + j] =
        a[i] === b[j] ? cell(i + 1, j + 1) + 1 : Math.max(cell(i + 1, j), cell(i, j + 1));
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] ?? "" });
      i++;
      j++;
    } else if (cell(i + 1, j) >= cell(i, j + 1)) {
      out.push({ kind: "removed", text: a[i] ?? "" });
      i++;
    } else {
      out.push({ kind: "added", text: b[j] ?? "" });
      j++;
    }
  }
  while (i < a.length) out.push({ kind: "removed", text: a[i++] ?? "" });
  while (j < b.length) out.push({ kind: "added", text: b[j++] ?? "" });
  return out;
}

/** Evidence rows whose `field_path` leading dot-segment is `field`. */
export function evidenceForField(evidence: EvidenceRecord[], field: string): EvidenceRecord[] {
  return evidence.filter((row) => row.field_path.split(".")[0] === field);
}

function resolveStorage(
  provided: ManifestReviewEditUIProps["storage"],
): Pick<Storage, "getItem" | "setItem" | "removeItem"> | null {
  if (provided !== undefined) return provided;
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

function readStoredDrafts(
  storage: Pick<Storage, "getItem"> | null,
  key: string,
): Record<string, StoredDraft> {
  if (!storage) return {};
  try {
    const raw = storage.getItem(key);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, StoredDraft> = {};
    for (const [field, entry] of Object.entries(parsed as Record<string, unknown>)) {
      const draft = entry as Partial<StoredDraft> | null;
      if (
        EDITABLE_FIELDS.includes(field) &&
        draft &&
        typeof draft.text === "string" &&
        typeof draft.baseText === "string"
      ) {
        out[field] = { text: draft.text, baseText: draft.baseText };
      }
    }
    return out;
  } catch {
    return {};
  }
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // fall through to the generic message below
  }
  return `request failed (${response.status})`;
}

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

/** Why Lock is unavailable; empty means every client-side gate is clear. */
export function lockBlockers(
  manifest: ManifestRecord,
  fieldStates: Record<string, FieldState>,
  draftTexts: Record<string, StoredDraft>,
): string[] {
  const blockers: string[] = [];
  if (manifest.status !== "review_required") {
    blockers.push("manifest must be submitted for review first");
  }
  for (const field of EDITABLE_FIELDS) {
    const state = fieldStates[field];
    if (state === "conflict") blockers.push(`${field}: unresolved conflict`);
    else if (state === "invalid") blockers.push(`${field}: invalid JSON`);
    else if (state === "unsaved") blockers.push(`${field}: unsaved changes`);
  }
  for (const field of CRITICAL_LOCK_FIELDS) {
    if (manifest[field] === null || manifest[field] === undefined) {
      blockers.push(`${field}: required before lock`);
    }
  }
  const binding = manifest[MODEL_BINDING_FIELD];
  if (binding !== null && binding !== undefined && !draftTexts[MODEL_BINDING_FIELD]) {
    const record = binding as Record<string, unknown>;
    if (typeof binding !== "object" || Array.isArray(binding)) {
      blockers.push("current model binding must be an object");
    } else if (record.resolved === false) {
      blockers.push("current model binding is unresolved");
    } else if (typeof record.role !== "string" || !MODEL_ROLES.includes(record.role)) {
      blockers.push("current model role not set to an explicit role");
    }
  }
  return blockers;
}

export function ManifestReviewEditUI({
  apiBaseUrl,
  organizationId,
  projectId,
  manifestId,
  storage: storageProp,
}: ManifestReviewEditUIProps) {
  const storage = useMemo(() => resolveStorage(storageProp), [storageProp]);
  const storageKey = draftStorageKey(organizationId, projectId, manifestId);

  const [manifest, setManifest] = useState<ManifestRecord | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, StoredDraft>>({});
  const [restored, setRestored] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [manifestResponse, evidenceResponse] = await Promise.all([
          fetch(manifestUrl(apiBaseUrl, organizationId, projectId, manifestId), {
            method: "GET",
            credentials: "include",
          }),
          fetch(evidenceUrl(apiBaseUrl, organizationId, projectId, manifestId), {
            method: "GET",
            credentials: "include",
          }),
        ]);
        if (!manifestResponse.ok) {
          if (!cancelled) setLoadError(await readErrorDetail(manifestResponse));
          return;
        }
        if (!evidenceResponse.ok) {
          // Without the evidence list the review would silently show
          // "no evidence" for everything -- refuse to render instead.
          if (!cancelled) setLoadError(await readErrorDetail(evidenceResponse));
          return;
        }
        const manifestBody: ManifestRecord = await manifestResponse.json();
        const evidenceBody: EvidenceRecord[] = await evidenceResponse.json();
        if (!manifestBody || typeof manifestBody.status !== "string") {
          if (!cancelled) setLoadError("malformed manifest response");
          return;
        }
        if (!Array.isArray(evidenceBody)) {
          if (!cancelled) setLoadError("malformed evidence response");
          return;
        }
        if (cancelled) return;
        const stored = readStoredDrafts(storage, storageKey);
        // A stored draft identical to the server text is not work to keep.
        const kept: Record<string, StoredDraft> = {};
        for (const [field, draft] of Object.entries(stored)) {
          if (draft.text !== serverText(manifestBody, field)) kept[field] = draft;
        }
        setManifest(manifestBody);
        setEvidence(evidenceBody);
        setDrafts(kept);
        setRestored(Object.keys(kept).length > 0);
      } catch (caught) {
        if (!cancelled) setLoadError(networkErrorMessage(caught));
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, organizationId, projectId, manifestId, storage, storageKey]);

  function persistLocally(next: Record<string, StoredDraft>) {
    if (!storage) return;
    try {
      if (Object.keys(next).length === 0) storage.removeItem(storageKey);
      else storage.setItem(storageKey, JSON.stringify(next));
    } catch {
      // Storage is a convenience; the server-side Save draft is the real persistence.
    }
  }

  function updateDrafts(next: Record<string, StoredDraft>) {
    setDrafts(next);
    persistLocally(next);
  }

  const fieldStates = useMemo(() => {
    const states: Record<string, FieldState> = {};
    if (!manifest) return states;
    for (const field of EDITABLE_FIELDS) {
      const draft = drafts[field];
      if (!draft) {
        states[field] = "clean";
      } else if (draft.baseText !== serverText(manifest, field)) {
        states[field] = "conflict";
      } else if (
        !(TEXT_FIELDS as readonly string[]).includes(field) &&
        !parseJsonDraft(draft.text).ok
      ) {
        states[field] = "invalid";
      } else {
        states[field] = "unsaved";
      }
    }
    return states;
  }, [manifest, drafts]);

  const editable = manifest?.status === "draft" || manifest?.status === "review_required";

  function handleEdit(field: string, text: string) {
    if (!manifest || !editable) return;
    const current = serverText(manifest, field);
    const next = { ...drafts };
    if (text === current) {
      delete next[field];
    } else {
      next[field] = { text, baseText: drafts[field]?.baseText ?? current };
    }
    updateDrafts(next);
  }

  function handleKeepMine(field: string) {
    if (!manifest) return;
    const mine = drafts[field];
    if (!mine) return;
    updateDrafts({
      ...drafts,
      [field]: { text: mine.text, baseText: serverText(manifest, field) },
    });
  }

  function handleTakeServer(field: string) {
    const next = { ...drafts };
    delete next[field];
    updateDrafts(next);
  }

  function handleRoleChange(role: string) {
    if (!manifest) return;
    const base = drafts[MODEL_BINDING_FIELD]
      ? parseJsonDraft(drafts[MODEL_BINDING_FIELD].text)
      : { ok: true as const, value: manifest[MODEL_BINDING_FIELD] };
    if (!base.ok || !base.value || typeof base.value !== "object" || Array.isArray(base.value)) {
      return;
    }
    handleEdit(
      MODEL_BINDING_FIELD,
      JSON.stringify({ ...(base.value as Record<string, unknown>), role }, null, 2),
    );
  }

  async function send(method: "PATCH" | "POST", suffix: string, body?: unknown) {
    setBusy(true);
    setActionError(null);
    try {
      const response = await fetch(
        manifestUrl(apiBaseUrl, organizationId, projectId, manifestId) + suffix,
        {
          method,
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      );
      if (!response.ok) {
        // Drafts stay in place: a refused request never discards work.
        setActionError(await readErrorDetail(response));
        return null;
      }
      const updated: ManifestRecord = await response.json();
      if (!updated || typeof updated.status !== "string") {
        setActionError("malformed manifest response");
        return null;
      }
      setManifest(updated);
      return updated;
    } catch (caught) {
      setActionError(networkErrorMessage(caught));
      return null;
    } finally {
      setBusy(false);
    }
  }

  const hasBlockingDraftState = Object.values(fieldStates).some(
    (state) => state === "conflict" || state === "invalid",
  );
  const unsavedFields = Object.keys(drafts);

  async function handleSaveDraft() {
    if (!manifest || unsavedFields.length === 0) return;
    const payload: Record<string, unknown> = {};
    for (const [field, draft] of Object.entries(drafts)) {
      const text = draft.text;
      if ((TEXT_FIELDS as readonly string[]).includes(field)) {
        payload[field] = text === "" ? null : text;
      } else {
        const parsed = parseJsonDraft(text);
        if (!parsed.ok) return;
        payload[field] = parsed.value;
      }
    }
    const updated = await send("PATCH", "", payload);
    if (updated) {
      const remaining = { ...drafts };
      for (const field of unsavedFields) delete remaining[field];
      updateDrafts(remaining);
      setRestored(false);
    }
  }

  async function handleSubmitForReview() {
    await send("POST", "/submit-for-review");
  }

  async function handleLock() {
    await send("POST", "/lock");
  }

  if (loadError) {
    return (
      <div data-testid="manifest-review-ui">
        <p role="alert" data-testid="manifest-load-error">
          {loadError}
        </p>
      </div>
    );
  }
  if (!manifest) {
    return (
      <div data-testid="manifest-review-ui">
        <p data-testid="manifest-loading">Loading manifest…</p>
      </div>
    );
  }

  const blockers = lockBlockers(manifest, fieldStates, drafts);
  const bindingForRole = drafts[MODEL_BINDING_FIELD]
    ? parseJsonDraft(drafts[MODEL_BINDING_FIELD].text)
    : { ok: true as const, value: manifest[MODEL_BINDING_FIELD] };
  const currentRole =
    bindingForRole.ok &&
    bindingForRole.value &&
    typeof bindingForRole.value === "object" &&
    typeof (bindingForRole.value as Record<string, unknown>).role === "string"
      ? ((bindingForRole.value as Record<string, unknown>).role as string)
      : "";
  const roleEditable =
    editable &&
    bindingForRole.ok &&
    !!bindingForRole.value &&
    typeof bindingForRole.value === "object" &&
    !Array.isArray(bindingForRole.value);

  return (
    <div data-testid="manifest-review-ui">
      <header>
        <p data-testid="manifest-status">Status: {manifest.status}</p>
        <p data-testid="manifest-version">Version: {manifest.version}</p>
        {manifest.manifest_sha256 && (
          <p data-testid="manifest-sha256">Locked hash: {manifest.manifest_sha256}</p>
        )}
        {!editable && (
          <p data-testid="manifest-read-only">
            This manifest is {manifest.status} and can no longer be edited.
          </p>
        )}
        {restored && (
          <p data-testid="draft-restored">
            Restored your unsaved edits from this browser. They are not saved to the server yet.
          </p>
        )}
      </header>

      {actionError && (
        <p role="alert" data-testid="manifest-action-error">
          {actionError}
        </p>
      )}

      <section data-testid="model-role-section">
        <label>
          Current model role
          <select
            data-testid="model-role-select"
            value={currentRole}
            disabled={!roleEditable}
            onChange={(event) => handleRoleChange(event.target.value)}
          >
            <option value="">Not set</option>
            {MODEL_ROLES.map((role) => (
              <option key={role} value={role}>
                {role}
              </option>
            ))}
          </select>
        </label>
      </section>

      {EDITABLE_FIELDS.map((field) => {
        const state = fieldStates[field];
        const draft = drafts[field];
        const current = serverText(manifest, field);
        const rows = evidenceForField(evidence, field);
        return (
          <section key={field} data-testid={`field-${field}`}>
            <h3>{field}</h3>
            <p data-testid={`field-state-${field}`}>{state}</p>
            <textarea
              data-testid={`field-input-${field}`}
              aria-label={field}
              value={draft ? draft.text : current}
              readOnly={!editable}
              onChange={(event) => handleEdit(field, event.target.value)}
            />

            {draft && state !== "conflict" && (
              <div data-testid={`field-overridden-${field}`}>
                <p>Overridden by you; not backed by detected evidence until saved and reviewed.</p>
                <pre data-testid={`field-diff-${field}`}>
                  {lineDiff(current, draft.text).map((line, index) => (
                    <span key={index} data-diff={line.kind} style={{ display: "block" }}>
                      {line.kind === "added" ? "+ " : line.kind === "removed" ? "- " : "  "}
                      {line.text}
                    </span>
                  ))}
                </pre>
              </div>
            )}

            {state === "conflict" && draft && (
              <div data-testid={`field-conflict-${field}`}>
                <p role="alert">
                  This field changed on the server after you started editing it. Choose which
                  version to keep.
                </p>
                <pre data-testid={`field-conflict-server-${field}`}>{current}</pre>
                <pre data-testid={`field-conflict-mine-${field}`}>{draft.text}</pre>
                <button
                  type="button"
                  data-testid={`keep-mine-${field}`}
                  onClick={() => handleKeepMine(field)}
                >
                  Keep mine
                </button>
                <button
                  type="button"
                  data-testid={`take-server-${field}`}
                  onClick={() => handleTakeServer(field)}
                >
                  Take server's
                </button>
              </div>
            )}

            <ul data-testid={`field-evidence-${field}`}>
              {rows.length === 0 && (
                <li data-testid={`field-no-evidence-${field}`}>No recorded evidence</li>
              )}
              {rows.map((row) => (
                <li key={row.id} data-testid={`evidence-${row.id}`}>
                  <span data-testid={`evidence-type-${row.id}`}>{row.evidence_type}</span>{" "}
                  {row.file_path && (
                    <span data-testid={`evidence-location-${row.id}`}>
                      {row.file_path}
                      {row.line_start !== null ? `:${row.line_start}-${row.line_end}` : ""}
                    </span>
                  )}{" "}
                  {row.symbol && (
                    <span data-testid={`evidence-symbol-${row.id}`}>{row.symbol}</span>
                  )}{" "}
                  {row.repository_commit_sha && (
                    <span data-testid={`evidence-commit-${row.id}`}>
                      {row.repository_commit_sha.slice(0, 12)}
                    </span>
                  )}{" "}
                  <span data-testid={`evidence-confidence-${row.id}`}>
                    {row.confidence_score === null
                      ? "confidence: n/a"
                      : `confidence: ${row.confidence_score}`}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}

      <footer>
        <button
          type="button"
          data-testid="save-draft-button"
          disabled={busy || !editable || hasBlockingDraftState || unsavedFields.length === 0}
          onClick={handleSaveDraft}
        >
          Save draft
        </button>
        <button
          type="button"
          data-testid="submit-for-review-button"
          disabled={busy || manifest.status !== "draft" || unsavedFields.length > 0}
          onClick={handleSubmitForReview}
        >
          Submit for review
        </button>
        <button
          type="button"
          data-testid="lock-button"
          disabled={busy || blockers.length > 0}
          onClick={handleLock}
        >
          Lock manifest
        </button>
        {blockers.length > 0 && (
          <ul data-testid="lock-blockers">
            {blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        )}
      </footer>
    </div>
  );
}
