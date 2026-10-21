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

/*
 * FTF-UX03 round 2 (UX_STANDARD.md): rebuilt on the UX01 components and tokens, every system string
 * through the en/ar catalogue (`strings_04a21.ts`) with `lang`/`dir` on every state, and every
 * failure shown in words with a way forward (never blank, never an unmounted page):
 * - A rejected fetch, a non-2xx reply, an unreadable body or a body that fails the strict parse
 *   (`parseManifest` / `parseEvidence`: wrong JSON type, value outside an enum, missing field) all
 *   end in a visible error with a Retry; nothing is rendered from a reply that failed the parse.
 * - Only a typed 2xx whose body is BOUND to what was sent counts as saved / submitted / locked
 *   (`unboundFields`): a reply that names another manifest, drops a sent field or changes its value
 *   is refused and the drafts stay (Law 17).
 * - The component is keyed by API base, organization, project and manifest, so every identity change
 *   resets drafts, results and in-flight state.
 */

import { useEffect, useId, useMemo, useState } from "react";

import { Banner, Button, Pending, dirFor, resolveLocale, type Locale } from "../ui";
import {
  MALFORMED_FAILURE,
  NETWORK_FAILURE,
  failureFromResponse,
  failureMessage,
  isRecord,
  type Failure,
} from "./common";
import { evidenceTypeLabel, fieldLabel, roleLabel, t04a21, type Key04a21 } from "./strings_04a21";

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

// Mirrors agent_manifest_schema.ALLOWED_STATUSES and manifest_evidence_schema.ALLOWED_EVIDENCE_TYPES.
export const MANIFEST_STATUSES = ["draft", "review_required", "locked", "superseded"] as const;
export const EVIDENCE_TYPES = [
  "repo_file",
  "repo_symbol",
  "config",
  "test",
  "runtime_probe",
  "user_input",
] as const;

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
  locale?: Locale;
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

export function serverText(manifest: ManifestRecord, field: string): string {
  const value = manifest[field];
  if ((TEXT_FIELDS as readonly string[]).includes(field)) {
    return typeof value === "string" ? value : "";
  }
  return JSON.stringify(value ?? null, null, 2);
}

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

export function evidenceForField(evidence: EvidenceRecord[], field: string): EvidenceRecord[] {
  return evidence.filter((row) => row.field_path.split(".")[0] === field);
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNumberOrNull(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

/**
 * Strict parse of a manifest reply: a wrong JSON type, a status outside the four known values or a
 * missing field is `null` (malformed), never a half-rendered manifest. Every content field must be
 * present (its value may be any JSON, `null` meaning "not set").
 */
export function parseManifest(body: unknown): ManifestRecord | null {
  if (!isRecord(body)) return null;
  if (typeof body.id !== "string" || body.id === "") return null;
  if (typeof body.project_id !== "string" || body.project_id === "") return null;
  if (
    typeof body.status !== "string" ||
    !(MANIFEST_STATUSES as readonly string[]).includes(body.status)
  ) {
    return null;
  }
  if (!Number.isInteger(body.version) || (body.version as number) < 1) return null;
  if (!Object.hasOwn(body, "manifest_sha256") || !isStringOrNull(body.manifest_sha256)) return null;
  if (!Object.hasOwn(body, "agent_name") || !isStringOrNull(body.agent_name)) return null;
  for (const field of JSON_FIELDS) {
    if (!Object.hasOwn(body, field)) return null;
  }
  return body as ManifestRecord;
}

const EVIDENCE_NULLABLE_STRINGS = [
  "repository_commit_sha",
  "file_path",
  "symbol",
  "content_hash",
] as const;
const EVIDENCE_NULLABLE_NUMBERS = ["line_start", "line_end", "confidence_score"] as const;

/** Strict parse of the evidence list: one bad row makes the whole list malformed (never "no evidence"). */
export function parseEvidence(body: unknown): EvidenceRecord[] | null {
  if (!Array.isArray(body)) return null;
  for (const row of body as unknown[]) {
    if (!isRecord(row)) return null;
    if (typeof row.id !== "string" || row.id === "") return null;
    if (typeof row.field_path !== "string") return null;
    if (
      typeof row.evidence_type !== "string" ||
      !(EVIDENCE_TYPES as readonly string[]).includes(row.evidence_type)
    ) {
      return null;
    }
    for (const key of EVIDENCE_NULLABLE_STRINGS) {
      if (!Object.hasOwn(row, key) || !isStringOrNull(row[key])) return null;
    }
    for (const key of EVIDENCE_NULLABLE_NUMBERS) {
      if (!Object.hasOwn(row, key) || !isNumberOrNull(row[key])) return null;
    }
  }
  return body as EvidenceRecord[];
}

function jsonEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, index) => jsonEqual(item, b[index]))
    );
  }
  if (isRecord(a) && isRecord(b)) {
    const keys = Object.keys(a);
    return (
      keys.length === Object.keys(b).length &&
      keys.every((key) => Object.hasOwn(b, key) && jsonEqual(a[key], b[key]))
    );
  }
  return false;
}

export type SentAction =
  | { kind: "patch"; sent: Record<string, unknown>; previousStatus: string }
  | { kind: "submit" }
  | { kind: "lock" };

/**
 * The one binding function: the names of everything in the reply that does NOT match what was
 * sent / expected. An empty list means the reply is bound; anything else is refused (the manifest is
 * not updated and nothing is shown as done).
 */
export function unboundFields(
  action: SentAction,
  reply: ManifestRecord,
  expected: { manifestId: string; projectId: string },
): string[] {
  const unbound: string[] = [];
  if (reply.id !== expected.manifestId) unbound.push("id");
  if (reply.project_id !== expected.projectId) unbound.push("project_id");
  if (action.kind === "patch") {
    if (reply.status !== action.previousStatus) unbound.push("status");
    for (const [field, sent] of Object.entries(action.sent)) {
      if (!jsonEqual(reply[field], sent)) unbound.push(field);
    }
  } else if (action.kind === "submit") {
    if (reply.status !== "review_required") unbound.push("status");
  } else {
    if (reply.status !== "locked") unbound.push("status");
    if (typeof reply.manifest_sha256 !== "string" || reply.manifest_sha256 === "") {
      unbound.push("manifest_sha256");
    }
  }
  return unbound;
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

type RequestResult = { ok: true; body: unknown } | { ok: false; failure: Failure };

/** One request that never throws: a rejected fetch, a non-2xx reply and an unreadable 2xx body are
 * each a typed failure. */
async function request(url: string, init: RequestInit): Promise<RequestResult> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    return { ok: false, failure: NETWORK_FAILURE };
  }
  if (!response.ok) return { ok: false, failure: await failureFromResponse(response) };
  try {
    return { ok: true, body: await response.json() };
  } catch {
    return { ok: false, failure: MALFORMED_FAILURE };
  }
}

export type LockBlocker =
  | { code: "needs_review" }
  | { code: "conflict" | "invalid" | "unsaved" | "required"; field: string }
  | { code: "binding_object" | "binding_unresolved" | "role" };

export function lockBlockers(
  manifest: ManifestRecord,
  fieldStates: Record<string, FieldState>,
  draftTexts: Record<string, StoredDraft>,
): LockBlocker[] {
  const blockers: LockBlocker[] = [];
  if (manifest.status !== "review_required") {
    blockers.push({ code: "needs_review" });
  }
  for (const field of EDITABLE_FIELDS) {
    const state = fieldStates[field];
    if (state === "conflict") blockers.push({ code: "conflict", field });
    else if (state === "invalid") blockers.push({ code: "invalid", field });
    else if (state === "unsaved") blockers.push({ code: "unsaved", field });
  }
  for (const field of CRITICAL_LOCK_FIELDS) {
    if (manifest[field] === null || manifest[field] === undefined) {
      blockers.push({ code: "required", field });
    }
  }
  const binding = manifest[MODEL_BINDING_FIELD];
  if (binding !== null && binding !== undefined && !draftTexts[MODEL_BINDING_FIELD]) {
    const record = binding as Record<string, unknown>;
    if (typeof binding !== "object" || Array.isArray(binding)) {
      blockers.push({ code: "binding_object" });
    } else if (record.resolved === false) {
      blockers.push({ code: "binding_unresolved" });
    } else if (typeof record.role !== "string" || !MODEL_ROLES.includes(record.role)) {
      blockers.push({ code: "role" });
    }
  }
  return blockers;
}

const BLOCKER_KEY: Record<LockBlocker["code"], Key04a21> = {
  needs_review: "blockNeedsReview",
  conflict: "blockConflict",
  invalid: "blockInvalid",
  unsaved: "blockUnsaved",
  required: "blockRequired",
  binding_object: "blockBindingObject",
  binding_unresolved: "blockBindingUnresolved",
  role: "blockRole",
};

export function blockerMessage(blocker: LockBlocker, locale: string): string {
  const field = "field" in blocker ? fieldLabel(blocker.field, locale) : "";
  return t04a21(BLOCKER_KEY[blocker.code], locale, { field });
}

type ActionKind = "patch" | "submit" | "lock";
type ActionFailure = Failure | { kind: "unbound" };

const STATUS_KEY: Record<(typeof MANIFEST_STATUSES)[number], Key04a21> = {
  draft: "statusDraft",
  review_required: "statusReviewRequired",
  locked: "statusLocked",
  superseded: "statusSuperseded",
};
// Status is text AND icon, never colour alone. UX01's StatusBadge vocabulary (section 72.2) has no
// "in review" / "locked" / "superseded" word, so the manifest lifecycle uses its own icon + word.
const STATUS_ICON: Record<(typeof MANIFEST_STATUSES)[number], string> = {
  draft: "✎",
  review_required: "…",
  locked: "■",
  superseded: "⊘",
};
const STATE_KEY: Record<FieldState, Key04a21> = {
  clean: "stateClean",
  unsaved: "stateUnsaved",
  invalid: "stateInvalid",
  conflict: "stateConflict",
};
const STATE_ICON: Record<FieldState, string> = {
  clean: "✓",
  unsaved: "●",
  invalid: "✕",
  conflict: "!",
};

const STACK = { display: "grid", gap: "var(--space-3)" } as const;
const ROW = {
  display: "flex",
  flexWrap: "wrap",
  gap: "var(--space-2)",
  alignItems: "center",
} as const;
const CODE_BLOCK = {
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  margin: 0,
  padding: "var(--space-2)",
  background: "var(--color-surface-2)",
  border: "1px solid var(--color-border)",
} as const;

export function ManifestReviewEditUI(props: ManifestReviewEditUIProps) {
  // Identity is part of the key: any change of API base, organization, project or manifest remounts
  // the inner component, so drafts, results, errors and in-flight state never leak across manifests.
  const identity = [props.apiBaseUrl, props.organizationId, props.projectId, props.manifestId];
  return <ManifestReviewEditInner key={identity.join("\u0000")} {...props} />;
}

function ManifestReviewEditInner({
  apiBaseUrl,
  organizationId,
  projectId,
  manifestId,
  storage: storageProp,
  locale = "en",
}: ManifestReviewEditUIProps) {
  const tt = (key: Key04a21, vars?: Record<string, string | number>) => t04a21(key, locale, vars);
  const uid = useId();
  const storage = useMemo(() => resolveStorage(storageProp), [storageProp]);
  const storageKey = draftStorageKey(organizationId, projectId, manifestId);

  const [manifest, setManifest] = useState<ManifestRecord | null>(null);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, StoredDraft>>({});
  const [restored, setRestored] = useState(false);
  const [actionFailure, setActionFailure] = useState<ActionFailure | null>(null);
  const [notice, setNotice] = useState<Key04a21 | null>(null);
  const [busyAction, setBusyAction] = useState<ActionKind | null>(null);
  const busy = busyAction !== null;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [manifestResult, evidenceResult] = await Promise.all([
        request(manifestUrl(apiBaseUrl, organizationId, projectId, manifestId), {
          method: "GET",
          credentials: "include",
        }),
        request(evidenceUrl(apiBaseUrl, organizationId, projectId, manifestId), {
          method: "GET",
          credentials: "include",
        }),
      ]);
      if (cancelled) return;
      if (!manifestResult.ok) {
        setLoadFailure(manifestResult.failure);
        return;
      }
      if (!evidenceResult.ok) {
        // Without the evidence list the review would silently show
        // "no evidence" for everything -- refuse to render instead.
        setLoadFailure(evidenceResult.failure);
        return;
      }
      const manifestBody = parseManifest(manifestResult.body);
      const evidenceBody = parseEvidence(evidenceResult.body);
      if (!manifestBody || !evidenceBody) {
        setLoadFailure(MALFORMED_FAILURE);
        return;
      }
      const stored = readStoredDrafts(storage, storageKey);
      // A stored draft identical to the server text is not work to keep.
      const kept: Record<string, StoredDraft> = {};
      for (const [field, draft] of Object.entries(stored)) {
        if (draft.text !== serverText(manifestBody, field)) kept[field] = draft;
      }
      setLoadFailure(null);
      setManifest(manifestBody);
      setEvidence(evidenceBody);
      setDrafts(kept);
      setRestored(Object.keys(kept).length > 0);
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl, organizationId, projectId, manifestId, storage, storageKey, loadAttempt]);

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
    setNotice(null);
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

  async function send(action: SentAction, suffix: string, body?: unknown) {
    if (!manifest) return null;
    setBusyAction(action.kind);
    setActionFailure(null);
    setNotice(null);
    try {
      const result = await request(
        manifestUrl(apiBaseUrl, organizationId, projectId, manifestId) + suffix,
        {
          method: action.kind === "patch" ? "PATCH" : "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      );
      if (!result.ok) {
        // Drafts stay in place: a refused request never discards work.
        setActionFailure(result.failure);
        return null;
      }
      const updated = parseManifest(result.body);
      if (!updated) {
        setActionFailure(MALFORMED_FAILURE);
        return null;
      }
      if (unboundFields(action, updated, { manifestId, projectId }).length > 0) {
        setActionFailure({ kind: "unbound" });
        return null;
      }
      setManifest(updated);
      return updated;
    } finally {
      setBusyAction(null);
    }
  }

  const hasBlockingDraftState = Object.values(fieldStates).some(
    (state) => state === "conflict" || state === "invalid",
  );
  const unsavedFields = Object.keys(drafts);

  async function handleSaveDraft() {
    if (!manifest || !editable || unsavedFields.length === 0) return;
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
    const updated = await send(
      { kind: "patch", sent: payload, previousStatus: manifest.status },
      "",
      payload,
    );
    if (updated) {
      const remaining = { ...drafts };
      for (const field of unsavedFields) delete remaining[field];
      updateDrafts(remaining);
      setRestored(false);
      setNotice("savedOk");
    }
  }

  async function handleSubmitForReview() {
    if ((await send({ kind: "submit" }, "/submit-for-review")) !== null) setNotice("submittedOk");
  }

  async function handleLock() {
    if ((await send({ kind: "lock" }, "/lock")) !== null) setNotice("lockedOk");
  }

  const shell = {
    "data-testid": "manifest-review-ui",
    lang: resolveLocale(locale),
    dir: dirFor(locale),
    "aria-busy": !manifest && !loadFailure ? true : busy ? true : undefined,
    style: STACK,
  } as const;

  if (loadFailure) {
    return (
      <div {...shell}>
        <div data-testid="manifest-load-error">
          <Banner kind="error" locale={locale}>
            {failureMessage(loadFailure, locale)}
          </Banner>
        </div>
        <div>
          <Button
            variant="primary"
            data-testid="manifest-retry-button"
            onClick={() => {
              setLoadFailure(null);
              setLoadAttempt((n) => n + 1);
            }}
          >
            {tt("retry")}
          </Button>
        </div>
      </div>
    );
  }
  if (!manifest) {
    return (
      <div {...shell}>
        <p data-testid="manifest-loading">{tt("loading")}</p>
        <Pending label={tt("loading")} locale={locale} />
      </div>
    );
  }

  const blockers = editable ? lockBlockers(manifest, fieldStates, drafts) : [];
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

  const status = manifest.status as (typeof MANIFEST_STATUSES)[number];
  const saveReason = !editable
    ? tt("whySaveReadOnly")
    : hasBlockingDraftState
      ? tt("whySaveBlocked")
      : unsavedFields.length === 0
        ? tt("whySaveNothing")
        : undefined;
  const submitReason =
    manifest.status !== "draft"
      ? tt("whySubmitStatus")
      : unsavedFields.length > 0
        ? tt("whySubmitUnsaved")
        : undefined;
  const lockReason = !editable
    ? tt("whySaveReadOnly")
    : blockers.length > 0
      ? tt("whyLockBlocked")
      : undefined;
  const busyLabel =
    busyAction === "patch"
      ? tt("saving")
      : busyAction === "submit"
        ? tt("submitting")
        : busyAction === "lock"
          ? tt("locking")
          : null;
  const roleId = `${uid}-role`;

  return (
    <div {...shell}>
      <header style={STACK}>
        <h2>{tt("title")}</h2>
        <p data-testid="manifest-status" data-manifest-status={manifest.status}>
          {tt("statusLabel")}: <span aria-hidden="true">{STATUS_ICON[status]}</span>{" "}
          <bdi>{tt(STATUS_KEY[status])}</bdi>
        </p>
        <p data-testid="manifest-version">
          {tt("versionLabel")}: <bdi>{manifest.version}</bdi>
        </p>
        {manifest.manifest_sha256 && (
          <p data-testid="manifest-sha256">
            {tt("hashLabel")}: <bdi dir="ltr">{manifest.manifest_sha256}</bdi>
          </p>
        )}
        {!editable && (
          <div data-testid="manifest-read-only">
            <Banner kind="info" locale={locale}>
              {tt(status === "superseded" ? "readOnlySuperseded" : "readOnlyLocked")}
            </Banner>
          </div>
        )}
        {restored && (
          <div data-testid="draft-restored">
            <Banner kind="info" locale={locale}>
              {tt("restored")}
            </Banner>
          </div>
        )}
      </header>

      {actionFailure && (
        <div data-testid="manifest-action-error">
          <Banner kind="error" locale={locale}>
            {actionFailure.kind === "unbound"
              ? tt("notBound")
              : failureMessage(actionFailure, locale)}
          </Banner>
        </div>
      )}
      {notice && (
        <div data-testid="manifest-action-notice">
          <Banner kind="success" locale={locale}>
            {tt(notice)}
          </Banner>
        </div>
      )}

      <section data-testid="model-role-section" style={{ display: "grid", gap: "var(--space-1)" }}>
        <label htmlFor={roleId}>{tt("modelRole")}</label>
        <div id={`${roleId}-help`}>{tt("modelRoleHelp")}</div>
        <select
          id={roleId}
          data-testid="model-role-select"
          aria-describedby={`${roleId}-help`}
          value={currentRole}
          disabled={!roleEditable}
          onChange={(event) => handleRoleChange(event.target.value)}
          style={{ minBlockSize: "var(--target-min)", maxInlineSize: "100%" }}
        >
          <option value="">{tt("roleNotSet")}</option>
          {MODEL_ROLES.map((role) => (
            <option key={role} value={role}>
              {roleLabel(role, locale)}
            </option>
          ))}
        </select>
      </section>

      {EDITABLE_FIELDS.map((field) => {
        const state = fieldStates[field] ?? "clean";
        const draft = drafts[field];
        const current = serverText(manifest, field);
        const rows = evidenceForField(evidence, field);
        const label = fieldLabel(field, locale);
        const inputId = `${uid}-${field}-input`;
        const stateId = `${uid}-${field}-state`;
        const errorId = `${uid}-${field}-error`;
        const conflictId = `${uid}-${field}-conflict`;
        const textField = (TEXT_FIELDS as readonly string[]).includes(field);
        const describedBy =
          [stateId, state === "invalid" ? errorId : null, state === "conflict" ? conflictId : null]
            .filter(Boolean)
            .join(" ") || undefined;
        return (
          <section
            key={field}
            data-testid={`field-${field}`}
            style={{ display: "grid", gap: "var(--space-2)" }}
          >
            <h3>
              <label htmlFor={inputId}>{label}</label>{" "}
              {CRITICAL_LOCK_FIELDS.includes(field) && <small>{tt("requiredToLock")}</small>}
            </h3>
            <p id={stateId} data-testid={`field-state-${field}`} data-state={state}>
              <span aria-hidden="true">{STATE_ICON[state]}</span> {tt(STATE_KEY[state])}
            </p>
            <textarea
              id={inputId}
              data-testid={`field-input-${field}`}
              dir={textField ? "auto" : "ltr"}
              value={draft ? draft.text : current}
              readOnly={!editable}
              aria-invalid={state === "invalid" ? true : undefined}
              aria-describedby={describedBy}
              rows={textField ? 1 : 6}
              onChange={(event) => handleEdit(field, event.target.value)}
              style={{ inlineSize: "100%", boxSizing: "border-box" }}
            />
            {state === "invalid" && (
              <div id={errorId} role="alert" data-testid={`field-error-${field}`}>
                {tt("invalidJson")}
              </div>
            )}

            {draft && state !== "conflict" && (
              <div
                data-testid={`field-overridden-${field}`}
                style={{ display: "grid", gap: "var(--space-2)" }}
              >
                <p>{tt("overridden")}</p>
                <p>{tt("diffHeading")}</p>
                <pre data-testid={`field-diff-${field}`} dir="ltr" style={CODE_BLOCK}>
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
              <div
                id={conflictId}
                data-testid={`field-conflict-${field}`}
                style={{ display: "grid", gap: "var(--space-2)" }}
              >
                <Banner kind="warning" locale={locale}>
                  {tt("conflictBody")}
                </Banner>
                <div>
                  <strong>{tt("serverVersion")}</strong>
                  <pre
                    data-testid={`field-conflict-server-${field}`}
                    dir={textField ? "auto" : "ltr"}
                    style={CODE_BLOCK}
                  >
                    {current}
                  </pre>
                </div>
                <div>
                  <strong>{tt("yourVersion")}</strong>
                  <pre
                    data-testid={`field-conflict-mine-${field}`}
                    dir={textField ? "auto" : "ltr"}
                    style={CODE_BLOCK}
                  >
                    {draft.text}
                  </pre>
                </div>
                <div style={ROW}>
                  <Button
                    data-testid={`keep-mine-${field}`}
                    aria-label={tt("keepMineFor", { field: label })}
                    onClick={() => handleKeepMine(field)}
                  >
                    {tt("keepMine")}
                  </Button>
                  <Button
                    data-testid={`take-server-${field}`}
                    aria-label={tt("takeServerFor", { field: label })}
                    onClick={() => handleTakeServer(field)}
                  >
                    {tt("takeServer")}
                  </Button>
                </div>
              </div>
            )}

            <div>
              <strong>{tt("evidenceHeading")}</strong>
              <ul data-testid={`field-evidence-${field}`}>
                {rows.length === 0 && (
                  <li data-testid={`field-no-evidence-${field}`}>{tt("noEvidence")}</li>
                )}
                {rows.map((row) => (
                  <li key={row.id} data-testid={`evidence-${row.id}`}>
                    <span data-testid={`evidence-type-${row.id}`}>
                      {evidenceTypeLabel(row.evidence_type, locale)}
                    </span>{" "}
                    {row.file_path && (
                      <bdi dir="ltr" data-testid={`evidence-location-${row.id}`}>
                        {row.file_path}
                        {row.line_start !== null
                          ? row.line_end !== null
                            ? `:${row.line_start}-${row.line_end}`
                            : `:${row.line_start}`
                          : ""}
                      </bdi>
                    )}{" "}
                    {row.symbol && (
                      <bdi dir="ltr" data-testid={`evidence-symbol-${row.id}`}>
                        {row.symbol}
                      </bdi>
                    )}{" "}
                    {row.repository_commit_sha && (
                      <>
                        <span>{tt("commit")}</span>{" "}
                        <bdi dir="ltr" data-testid={`evidence-commit-${row.id}`}>
                          {row.repository_commit_sha.slice(0, 12)}
                        </bdi>
                      </>
                    )}{" "}
                    <span data-testid={`evidence-confidence-${row.id}`}>
                      {row.confidence_score === null
                        ? tt("confidenceNA")
                        : tt("confidence", { score: row.confidence_score })}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        );
      })}

      <footer style={STACK}>
        {busyLabel && <Pending label={busyLabel} locale={locale} />}
        <div style={ROW}>
          <Button
            variant="primary"
            data-testid="save-draft-button"
            disabled={busy}
            disabledReason={saveReason}
            onClick={handleSaveDraft}
          >
            {tt("saveDraft")}
          </Button>
          <Button
            data-testid="submit-for-review-button"
            disabled={busy}
            disabledReason={submitReason}
            onClick={handleSubmitForReview}
          >
            {tt("submitForReview")}
          </Button>
          <Button
            data-testid="lock-button"
            disabled={busy}
            disabledReason={lockReason}
            onClick={handleLock}
          >
            {tt("lockManifest")}
          </Button>
        </div>
        {blockers.length > 0 && (
          <div>
            <p>{tt("blockersHeading")}</p>
            <ul data-testid="lock-blockers">
              {blockers.map((blocker) => (
                <li key={`${blocker.code}:${"field" in blocker ? blocker.field : ""}`}>
                  {blockerMessage(blocker, locale)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </footer>
    </div>
  );
}
