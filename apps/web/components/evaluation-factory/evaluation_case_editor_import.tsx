/**
 * Evaluation case editor/import UI (FTF-0602, Section 6.14/23-24/26-27/69.1-69.2/78.2).
 *
 * Consumes only the frozen FTF-0602-P1 routes in
 * `packages/contracts/openapi/06-interface.yaml`:
 *
 *   GET   .../evaluation-suites/{suite}/cases?limit&after
 *   POST  .../evaluation-suites/{suite}/cases          {cases: [1..500]}
 *   GET   .../evaluation-suites/{suite}/cases/{case}
 *   PATCH .../evaluation-suites/{suite}/cases/{case}
 *
 * Acceptance ("UI/import defines cases, independent expected state,
 * severity/category/language and grouping"):
 *
 * - Import parses a pasted or uploaded JSON array / JSON Lines file in the
 *   browser (the server never parses files) and sends one all-or-nothing
 *   POST. Every case carries its own `expected_json`, `severity`,
 *   `category`, `language` and `group_key`; nothing is defaulted here, so a
 *   missing field is the server's `required` error, not a silent fill.
 * - The editor changes only the fields the user touched (PATCH), and never
 *   `case_key`, which is immutable.
 *
 * Negative acceptance ("Model self-report as sole critical success oracle is
 * rejected; final answers unavailable to training roles"):
 *
 * - A case with `critical: true` and a grader that is not an independent
 *   oracle (anything but tool_name, tool_arguments, tool_sequence,
 *   state_transition, forbidden_action, required_refusal) is blocked in the
 *   browser before any request and, independently, the route's
 *   `critical_oracle_not_independent` 400 is shown as an error. The server
 *   is the enforcement; the browser check only saves a round trip.
 * - A 403 `final_suite_content_restricted` clears every case from the
 *   screen and renders no content, so a training role never sees final
 *   answers; a UI control never authorizes anything.
 * - Validation errors name the row, field and code only; the submitted
 *   value is never echoed. Provider/network failures are errors, never a
 *   successful import (Law 17).
 *
 * Scope disclosed, not hidden (same precedent as
 * `model_to_artifact_mapping_ui.tsx`): this repository has no Next.js `app/`
 * page tree yet (FTF-H029), so this component is DOM-tested with Vitest +
 * React Testing Library + jsdom and a mocked `fetch`; it is UNWIRED to a page
 * until that mount exists.
 */

import { useState } from "react";

export const GRADER_TYPES = [
  "exact_match",
  "json_schema",
  "tool_name",
  "tool_arguments",
  "tool_sequence",
  "state_transition",
  "forbidden_action",
  "required_refusal",
  "contains_fact",
  "numeric_tolerance",
  "latency",
  "cost",
  "llm_judge",
] as const;

/** Section 27 / 69.2: the graders whose verdict does not rest on model self-report. */
export const INDEPENDENT_CRITICAL_GRADERS: readonly string[] = [
  "tool_name",
  "tool_arguments",
  "tool_sequence",
  "state_transition",
  "forbidden_action",
  "required_refusal",
];

export const SEVERITIES = ["low", "medium", "high", "critical"] as const;
export const MAX_IMPORT_CASES = 500;

export interface EvaluationCase {
  id: string;
  case_key: string;
  category: string;
  severity: (typeof SEVERITIES)[number];
  critical: boolean;
  language: string;
  group_key: string;
  input_json: Record<string, unknown>;
  expected_json: Record<string, unknown>;
  grader_type: (typeof GRADER_TYPES)[number];
  grader_config_json: Record<string, unknown>;
  weight: number;
}

export interface EvaluationCaseEditorImportProps {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  suiteId: string;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

export function casesUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  suiteId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/projects/${projectId}/evaluation-suites/${suiteId}/cases`
  );
}

export function caseUrl(
  apiBaseUrl: string,
  organizationId: string,
  projectId: string,
  suiteId: string,
  caseId: string,
): string {
  return `${casesUrl(apiBaseUrl, organizationId, projectId, suiteId)}/${caseId}`;
}

const ERROR_MESSAGES: Record<string, string> = {
  not_found: "The suite or case was not found.",
  suite_locked: "The suite is locked; cases can no longer be changed.",
  duplicate_case_key: "A case with that case_key already exists; nothing was imported.",
  role_not_authorized: "Your role may not do this.",
  final_suite_content_restricted:
    "This is a final suite; its case content is restricted to evaluators and administrators.",
  validation_failed: "The request failed validation; nothing was saved.",
  invalid_request: "The request was not valid; nothing was saved.",
  invalid_query: "The page request was not valid.",
  body_too_large: "The request is too large; nothing was saved.",
};

interface FieldError {
  index: number | null;
  field: string | null;
  code: string;
}

interface Failure {
  message: string;
  fieldErrors: FieldError[];
  restricted: boolean;
}

async function readFailure(response: Response): Promise<Failure> {
  let detail: string | null = null;
  const fieldErrors: FieldError[] = [];
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") detail = body.detail;
    if (body && Array.isArray(body.errors)) {
      for (const entry of body.errors) {
        if (entry && typeof entry.code === "string") {
          // Only the row, field and code are kept; a value is never read.
          fieldErrors.push({
            index: typeof entry.index === "number" ? entry.index : null,
            field: typeof entry.field === "string" ? entry.field : null,
            code: entry.code,
          });
        }
      }
    }
  } catch {
    // fall through to the generic message below
  }
  return {
    message: detail ? (ERROR_MESSAGES[detail] ?? detail) : `request failed (${response.status})`,
    fieldErrors,
    restricted: detail === "final_suite_content_restricted",
  };
}

function networkFailure(caught: unknown): Failure {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return { message: `network error: ${reason}`, fieldErrors: [], restricted: false };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCase(value: unknown): value is EvaluationCase {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.case_key === "string" &&
    typeof value.grader_type === "string" &&
    typeof value.critical === "boolean" &&
    isRecord(value.input_json) &&
    isRecord(value.expected_json)
  );
}

/** Parse a JSON array or a JSON Lines file. Returns the cases or a reason. */
export function parseImportText(
  text: string,
): { cases: Record<string, unknown>[] } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === "") return { error: "Paste or choose a file with 1 to 500 cases." };
  let rows: unknown[];
  if (trimmed.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (!Array.isArray(parsed)) return { error: "The file is not a JSON array." };
      rows = parsed;
    } catch {
      return { error: "The file is not valid JSON." };
    }
  } else {
    rows = [];
    const lines = trimmed.split(/\r?\n/);
    for (const [position, line] of lines.entries()) {
      if (line.trim() === "") continue;
      try {
        rows.push(JSON.parse(line));
      } catch {
        return { error: `Line ${position + 1} is not valid JSON.` };
      }
    }
  }
  if (rows.length < 1 || rows.length > MAX_IMPORT_CASES) {
    return { error: `An import takes 1 to ${MAX_IMPORT_CASES} cases; this has ${rows.length}.` };
  }
  for (let i = 0; i < rows.length; i += 1) {
    if (!isRecord(rows[i])) return { error: `Row ${i + 1} is not a JSON object.` };
  }
  return { cases: rows as Record<string, unknown>[] };
}

/** Rows whose own fields make a model self-report the sole critical oracle. */
export function nonIndependentCriticalRows(cases: Record<string, unknown>[]): number[] {
  const rows: number[] = [];
  cases.forEach((row, index) => {
    if (row.critical === true && !INDEPENDENT_CRITICAL_GRADERS.includes(String(row.grader_type))) {
      rows.push(index);
    }
  });
  return rows;
}

function parseObjectField(text: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(text);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

interface Draft {
  category: string;
  severity: string;
  critical: boolean;
  language: string;
  group_key: string;
  input_json: string;
  expected_json: string;
  grader_type: string;
  grader_config_json: string;
  weight: string;
}

function draftFrom(item: EvaluationCase): Draft {
  return {
    category: item.category,
    severity: item.severity,
    critical: item.critical,
    language: item.language,
    group_key: item.group_key,
    input_json: JSON.stringify(item.input_json, null, 2),
    expected_json: JSON.stringify(item.expected_json, null, 2),
    grader_type: item.grader_type,
    grader_config_json: JSON.stringify(item.grader_config_json ?? {}, null, 2),
    weight: String(item.weight),
  };
}

/** Only the fields the user changed; case_key is never part of a patch. */
function buildPatch(
  original: EvaluationCase,
  draft: Draft,
): { patch: Record<string, unknown> } | { error: string } {
  const patch: Record<string, unknown> = {};
  for (const key of ["category", "severity", "language", "group_key", "grader_type"] as const) {
    if (draft[key] !== original[key]) patch[key] = draft[key];
  }
  if (draft.critical !== original.critical) patch.critical = draft.critical;
  for (const key of ["input_json", "expected_json", "grader_config_json"] as const) {
    const parsed = parseObjectField(draft[key]);
    if (parsed === null) return { error: `${key} must be a JSON object.` };
    if (key === "expected_json" && Object.keys(parsed).length === 0) {
      return { error: "expected_json must not be empty." };
    }
    if (JSON.stringify(parsed) !== JSON.stringify(original[key] ?? {})) patch[key] = parsed;
  }
  const weight = Number(draft.weight);
  if (draft.weight.trim() === "" || !Number.isFinite(weight) || weight <= 0) {
    return { error: "weight must be a number greater than 0." };
  }
  if (weight !== original.weight) patch.weight = weight;
  return { patch };
}

export function EvaluationCaseEditorImport({
  apiBaseUrl,
  organizationId,
  projectId,
  suiteId,
}: EvaluationCaseEditorImportProps) {
  const [cases, setCases] = useState<EvaluationCase[] | null>(null);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [restricted, setRestricted] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [importText, setImportText] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importFieldErrors, setImportFieldErrors] = useState<FieldError[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const [editing, setEditing] = useState<EvaluationCase | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editFieldErrors, setEditFieldErrors] = useState<FieldError[]>([]);

  const listUrl = casesUrl(apiBaseUrl, organizationId, projectId, suiteId);

  function restrictContent() {
    setRestricted(true);
    setCases(null);
    setNextAfter(null);
    setEditing(null);
  }

  async function loadPage(after: string | null) {
    setListError(null);
    const query = after === null ? "" : `?after=${encodeURIComponent(after)}`;
    try {
      const response = await fetch(`${listUrl}${query}`, {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        const failure = await readFailure(response);
        if (failure.restricted) restrictContent();
        setListError(failure.message);
        return;
      }
      const body: unknown = await response.json();
      if (
        !isRecord(body) ||
        !Array.isArray(body.items) ||
        !body.items.every(isCase) ||
        !(body.next_after === null || typeof body.next_after === "string")
      ) {
        setListError("malformed case listing response");
        return;
      }
      setRestricted(false);
      setCases((previous) =>
        after === null
          ? (body.items as EvaluationCase[])
          : [...(previous ?? []), ...(body.items as EvaluationCase[])],
      );
      setNextAfter(body.next_after as string | null);
    } catch (caught) {
      setListError(networkFailure(caught).message);
    }
  }

  function handleFile(file: File | undefined) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImportText(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => setImportError("The file could not be read.");
    reader.readAsText(file);
  }

  async function handleImport() {
    setImportError(null);
    setImportFieldErrors([]);
    setNotice(null);
    const parsed = parseImportText(importText);
    if ("error" in parsed) {
      setImportError(parsed.error);
      return;
    }
    const unsafe = nonIndependentCriticalRows(parsed.cases);
    if (unsafe.length > 0) {
      setImportError(
        `Row ${unsafe.map((index) => index + 1).join(", ")}: a critical case needs an independent ` +
          "grader (tool or state check), not a model self-report; nothing was sent.",
      );
      return;
    }
    setImporting(true);
    try {
      const response = await fetch(listUrl, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cases: parsed.cases }),
      });
      if (!response.ok) {
        const failure = await readFailure(response);
        if (failure.restricted) restrictContent();
        setImportError(failure.message);
        setImportFieldErrors(failure.fieldErrors);
        return;
      }
      const body: unknown = await response.json();
      if (!isRecord(body) || !Array.isArray(body.items) || !body.items.every(isCase)) {
        setImportError("malformed import response");
        return;
      }
      const created = body.items as EvaluationCase[];
      setImportText("");
      setNotice(`Imported ${created.length} case${created.length === 1 ? "" : "s"}.`);
      setCases((previous) =>
        previous === null
          ? previous
          : [...previous, ...created].sort((a, b) => (a.case_key < b.case_key ? -1 : 1)),
      );
    } catch (caught) {
      // Nothing is shown as imported unless the route genuinely answered 201.
      setImportError(networkFailure(caught).message);
    } finally {
      setImporting(false);
    }
  }

  function startEdit(item: EvaluationCase) {
    setEditing(item);
    setDraft(draftFrom(item));
    setEditError(null);
    setEditFieldErrors([]);
    setNotice(null);
  }

  function updateDraft(change: Partial<Draft>) {
    setDraft((previous) => (previous === null ? previous : { ...previous, ...change }));
  }

  async function handleSave() {
    if (editing === null || draft === null) return;
    setEditError(null);
    setEditFieldErrors([]);
    const built = buildPatch(editing, draft);
    if ("error" in built) {
      setEditError(built.error);
      return;
    }
    if (Object.keys(built.patch).length === 0) {
      setEditError("Nothing was changed.");
      return;
    }
    const effectiveCritical = (built.patch.critical ?? editing.critical) === true;
    const effectiveGrader = String(built.patch.grader_type ?? editing.grader_type);
    if (effectiveCritical && !INDEPENDENT_CRITICAL_GRADERS.includes(effectiveGrader)) {
      setEditError(
        "A critical case needs an independent grader (tool or state check), not a model self-report; nothing was sent.",
      );
      return;
    }
    setSaving(true);
    try {
      const response = await fetch(
        caseUrl(apiBaseUrl, organizationId, projectId, suiteId, editing.id),
        {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(built.patch),
        },
      );
      if (!response.ok) {
        const failure = await readFailure(response);
        if (failure.restricted) restrictContent();
        setEditError(failure.message);
        setEditFieldErrors(failure.fieldErrors);
        return;
      }
      const body: unknown = await response.json();
      if (!isCase(body)) {
        setEditError("malformed case response");
        return;
      }
      setCases((previous) =>
        previous === null ? previous : previous.map((item) => (item.id === body.id ? body : item)),
      );
      setEditing(body);
      setDraft(draftFrom(body));
      setNotice(`Saved ${body.case_key}.`);
    } catch (caught) {
      setEditError(networkFailure(caught).message);
    } finally {
      setSaving(false);
    }
  }

  function renderFieldErrors(errors: FieldError[], testId: string) {
    if (errors.length === 0) return null;
    return (
      <ul data-testid={testId}>
        {errors.map((entry, position) => (
          <li key={position}>
            {entry.index === null ? "Request" : `Row ${entry.index + 1}`}
            {entry.field === null ? "" : ` / ${entry.field}`}: {entry.code}
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section aria-label="Evaluation cases">
      <h2>Evaluation cases</h2>

      <div>
        <button type="button" data-testid="load-cases-button" onClick={() => loadPage(null)}>
          Load cases
        </button>
        {listError ? (
          <p role="alert" data-testid="list-error">
            {listError}
          </p>
        ) : null}
        {restricted ? (
          <p data-testid="restricted-notice">
            Case content for this final suite is restricted. No case content is shown.
          </p>
        ) : null}
        {cases !== null ? (
          <ul data-testid="case-list">
            {cases.map((item) => (
              <li key={item.id} data-testid={`case-row-${item.id}`}>
                <span data-testid={`case-key-${item.id}`}>{item.case_key}</span>{" "}
                <span>{item.category}</span> <span>{item.severity}</span>
                {item.critical ? <strong> critical</strong> : null} <span>{item.language}</span>{" "}
                <span>group {item.group_key}</span> <span>{item.grader_type}</span>{" "}
                <button
                  type="button"
                  data-testid={`edit-case-${item.id}`}
                  onClick={() => startEdit(item)}
                >
                  Edit
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {nextAfter !== null ? (
          <button type="button" data-testid="load-more-button" onClick={() => loadPage(nextAfter)}>
            Load more
          </button>
        ) : null}
      </div>

      <div>
        <h3>Import cases</h3>
        <p>
          A JSON array or a JSON Lines file of 1 to {MAX_IMPORT_CASES} cases. Each case needs its
          own expected state, severity, category, language and group key. Import is all or nothing.
        </p>
        <input
          type="file"
          accept=".json,.jsonl,application/json"
          data-testid="import-file"
          onChange={(event) => handleFile(event.target.files?.[0])}
        />
        <textarea
          data-testid="import-text"
          aria-label="Cases to import"
          value={importText}
          onChange={(event) => setImportText(event.target.value)}
        />
        <button
          type="button"
          data-testid="import-button"
          disabled={importing}
          onClick={handleImport}
        >
          Import cases
        </button>
        {importError ? (
          <p role="alert" data-testid="import-error">
            {importError}
          </p>
        ) : null}
        {renderFieldErrors(importFieldErrors, "import-field-errors")}
        {notice ? (
          <p role="status" data-testid="notice">
            {notice}
          </p>
        ) : null}
      </div>

      {editing !== null && draft !== null ? (
        <form
          data-testid="case-form"
          onSubmit={(event) => {
            event.preventDefault();
            handleSave();
          }}
        >
          <h3>
            Edit case <span data-testid="edit-case-key">{editing.case_key}</span>
          </h3>
          <label>
            Category
            <input
              data-testid="field-category"
              value={draft.category}
              onChange={(event) => updateDraft({ category: event.target.value })}
            />
          </label>
          <label>
            Severity
            <select
              data-testid="field-severity"
              value={draft.severity}
              onChange={(event) => updateDraft({ severity: event.target.value })}
            >
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {severity}
                </option>
              ))}
            </select>
          </label>
          <label>
            Critical
            <input
              type="checkbox"
              data-testid="field-critical"
              checked={draft.critical}
              onChange={(event) => updateDraft({ critical: event.target.checked })}
            />
          </label>
          <label>
            Language
            <input
              data-testid="field-language"
              value={draft.language}
              onChange={(event) => updateDraft({ language: event.target.value })}
            />
          </label>
          <label>
            Group key
            <input
              data-testid="field-group-key"
              value={draft.group_key}
              onChange={(event) => updateDraft({ group_key: event.target.value })}
            />
          </label>
          <label>
            Grader
            <select
              data-testid="field-grader-type"
              value={draft.grader_type}
              onChange={(event) => updateDraft({ grader_type: event.target.value })}
            >
              {GRADER_TYPES.map((grader) => (
                <option key={grader} value={grader}>
                  {grader}
                </option>
              ))}
            </select>
          </label>
          <label>
            Input (JSON)
            <textarea
              data-testid="field-input-json"
              value={draft.input_json}
              onChange={(event) => updateDraft({ input_json: event.target.value })}
            />
          </label>
          <label>
            Expected state (JSON)
            <textarea
              data-testid="field-expected-json"
              value={draft.expected_json}
              onChange={(event) => updateDraft({ expected_json: event.target.value })}
            />
          </label>
          <label>
            Grader config (JSON)
            <textarea
              data-testid="field-grader-config-json"
              value={draft.grader_config_json}
              onChange={(event) => updateDraft({ grader_config_json: event.target.value })}
            />
          </label>
          <label>
            Weight
            <input
              data-testid="field-weight"
              value={draft.weight}
              onChange={(event) => updateDraft({ weight: event.target.value })}
            />
          </label>
          <button type="submit" data-testid="save-case-button" disabled={saving}>
            Save case
          </button>
          {editError ? (
            <p role="alert" data-testid="edit-error">
              {editError}
            </p>
          ) : null}
          {renderFieldErrors(editFieldErrors, "edit-field-errors")}
        </form>
      ) : null}
    </section>
  );
}
