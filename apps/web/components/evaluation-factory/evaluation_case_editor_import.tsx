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
 * FTF-UX06 round 1 (docs/design/audit-2026-10.md section 8, cross-cutting
 * X1/X3/X4/X7): rebuilt on the certified `apps/web/components/ui/`
 * library and `tokens.css` (X1); full bilingual en/ar + RTL via a `locale`
 * prop and `evaluation-factory/strings_0602.ts` (X4); every field-level
 * error is now connected to the specific input it names via
 * `aria-describedby`, not just listed (X3); loading/importing/saving use
 * `aria-busy` immediately and the shared `Pending` indicator once an
 * operation runs past NN/g's 1 s threshold (X7); the three JSON fields now
 * carry a schema hint and validate on blur, before submit (per-component
 * H5); an import field error now names the affected row's own `case_key`
 * alongside its row number, not just the row index (per-component H9).
 * `Field`/`StatusBadge` are not used directly: `Field` only wraps
 * `<input>` (none of this screen's `<select>`/`<textarea>` fields fit it)
 * and no `StatusBadge` state word describes an evaluation case; their
 * exact label/helper/error/`aria-describedby` mechanism is replicated
 * inline instead of adding new shared primitives, which is out of this
 * round's scope (disclosed, not hidden, matching FTF-UX03 round 1's own
 * disclosed chip-editor deferral). `DataTable` is not used for the case
 * list either: its `<tr>` offers no per-row test hook, and the certified
 * `ftf_0602.spec.ts` suite asserts on one `case-row-{id}` element holding
 * the whole row's text; the list keeps its `<ul>`/`<li>` structure
 * (`ftf_0602.spec.ts` already tolerates either, querying `li, tr`).
 *
 * Scope disclosed, not hidden (same precedent as
 * `model_to_artifact_mapping_ui.tsx`): this repository has no Next.js
 * `app/` page tree yet (FTF-H029), so this component is DOM-tested with
 * Vitest + React Testing Library + jsdom and a mocked `fetch`; it is
 * UNWIRED to a page until that mount exists. The real-browser §72.5
 * viewport/keyboard/200%-zoom and automated-accessibility-scan evidence is
 * deferred the same way FTF-UX03 round 1 deferred it ("needs a rendered
 * page"; not claimed this round).
 */

import { useId, useState } from "react";

import { Button, EmptyState, Pending } from "../ui";
import { dirFor, resolveLocale, t0602, type Locale } from "./strings_0602";

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
  /** UX_STANDARD.md X4: standard phrases are bilingual without asking; defaults to English. */
  locale?: Locale;
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

/** Maps a backend `detail` code to its catalogue key; an unknown code falls through to `errGeneric`. */
const ERROR_DETAIL_KEY: Record<string, Parameters<typeof t0602>[0]> = {
  not_found: "errNotFound",
  suite_locked: "errSuiteLocked",
  duplicate_case_key: "errDuplicateCaseKey",
  role_not_authorized: "errRoleNotAuthorized",
  final_suite_content_restricted: "errFinalRestricted",
  validation_failed: "errValidationFailed",
  invalid_request: "errInvalidRequest",
  invalid_query: "errInvalidQuery",
  body_too_large: "errBodyTooLarge",
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

async function readFailure(response: Response, locale: Locale | string = "en"): Promise<Failure> {
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
  const key = detail ? ERROR_DETAIL_KEY[detail] : undefined;
  const message = key
    ? t0602(key, locale)
    : detail
      ? detail
      : t0602("errGeneric", locale, { status: response.status });
  return {
    message,
    fieldErrors,
    restricted: detail === "final_suite_content_restricted",
  };
}

function networkFailure(caught: unknown, locale: Locale | string = "en"): Failure {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return { message: t0602("errNetwork", locale, { reason }), fieldErrors: [], restricted: false };
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
  locale: Locale | string = "en",
): { cases: Record<string, unknown>[] } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === "") return { error: t0602("parseEmpty", locale, { max: MAX_IMPORT_CASES }) };
  let rows: unknown[];
  if (trimmed.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (!Array.isArray(parsed)) return { error: t0602("parseNotArray", locale) };
      rows = parsed;
    } catch {
      return { error: t0602("parseNotJson", locale) };
    }
  } else {
    rows = [];
    const lines = trimmed.split(/\r?\n/);
    for (const [position, line] of lines.entries()) {
      if (line.trim() === "") continue;
      try {
        rows.push(JSON.parse(line));
      } catch {
        return { error: t0602("parseLineInvalid", locale, { line: position + 1 }) };
      }
    }
  }
  if (rows.length < 1 || rows.length > MAX_IMPORT_CASES) {
    return {
      error: t0602("parseCountRange", locale, { max: MAX_IMPORT_CASES, count: rows.length }),
    };
  }
  for (let i = 0; i < rows.length; i += 1) {
    if (!isRecord(rows[i])) return { error: t0602("parseRowNotObject", locale, { row: i + 1 }) };
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
  locale: Locale | string = "en",
): { patch: Record<string, unknown> } | { error: string } {
  const patch: Record<string, unknown> = {};
  for (const key of ["category", "severity", "language", "group_key", "grader_type"] as const) {
    if (draft[key] !== original[key]) patch[key] = draft[key];
  }
  if (draft.critical !== original.critical) patch.critical = draft.critical;
  for (const key of ["input_json", "expected_json", "grader_config_json"] as const) {
    const parsed = parseObjectField(draft[key]);
    if (parsed === null) return { error: t0602("jsonFieldMustBeObject", locale, { field: key }) };
    if (key === "expected_json" && Object.keys(parsed).length === 0) {
      return { error: t0602("expectedMustNotBeEmpty", locale) };
    }
    if (JSON.stringify(parsed) !== JSON.stringify(original[key] ?? {})) patch[key] = parsed;
  }
  const weight = Number(draft.weight);
  if (draft.weight.trim() === "" || !Number.isFinite(weight) || weight <= 0) {
    return { error: t0602("weightMustBePositive", locale) };
  }
  if (weight !== original.weight) patch.weight = weight;
  return { patch };
}

/** "Row N" / "Request", localized; the word-order itself never changes (tests match it verbatim). */
function rowOrRequest(index: number | null, locale: Locale | string): string {
  return index === null ? t0602("requestWord", locale) : `${t0602("rowWord", locale)} ${index + 1}`;
}

export function EvaluationCaseEditorImport({
  apiBaseUrl,
  organizationId,
  projectId,
  suiteId,
  locale = "en",
}: EvaluationCaseEditorImportProps) {
  const lang = resolveLocale(locale);
  const dir = dirFor(lang);
  const idPrefix = useId();

  const [cases, setCases] = useState<EvaluationCase[] | null>(null);
  const [nextAfter, setNextAfter] = useState<string | null>(null);
  const [restricted, setRestricted] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(false);

  const [importText, setImportText] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importFieldErrors, setImportFieldErrors] = useState<FieldError[]>([]);
  /** The exact rows last sent, kept so a returned field error can name that row's own case_key
   * (per-component H9: a visual link back to the pasted row, not just an index). */
  const [importRows, setImportRows] = useState<Record<string, unknown>[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const [editing, setEditing] = useState<EvaluationCase | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editFieldErrors, setEditFieldErrors] = useState<FieldError[]>([]);
  /** on-blur validation for the three JSON fields, independent of (and before) submit-time checks. */
  const [jsonBlurErrors, setJsonBlurErrors] = useState<Record<string, string>>({});

  const listUrl = casesUrl(apiBaseUrl, organizationId, projectId, suiteId);

  function restrictContent() {
    setRestricted(true);
    setCases(null);
    setNextAfter(null);
    setEditing(null);
  }

  async function loadPage(after: string | null) {
    setListError(null);
    setListLoading(true);
    const query = after === null ? "" : `?after=${encodeURIComponent(after)}`;
    try {
      const response = await fetch(`${listUrl}${query}`, {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        const failure = await readFailure(response, locale);
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
        setListError(t0602("errMalformedListing", locale));
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
      setListError(networkFailure(caught, locale).message);
    } finally {
      setListLoading(false);
    }
  }

  function handleFile(file: File | undefined) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImportText(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => setImportError(t0602("fileReadError", locale));
    reader.readAsText(file);
  }

  async function handleImport() {
    setImportError(null);
    setImportFieldErrors([]);
    setNotice(null);
    const parsed = parseImportText(importText, locale);
    if ("error" in parsed) {
      setImportError(parsed.error);
      return;
    }
    const unsafe = nonIndependentCriticalRows(parsed.cases);
    if (unsafe.length > 0) {
      setImportError(
        t0602("criticalBlockedImport", locale, {
          rows: unsafe.map((index) => index + 1).join(", "),
        }),
      );
      return;
    }
    setImporting(true);
    setImportRows(parsed.cases);
    try {
      const response = await fetch(listUrl, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cases: parsed.cases }),
      });
      if (!response.ok) {
        const failure = await readFailure(response, locale);
        if (failure.restricted) restrictContent();
        setImportError(failure.message);
        setImportFieldErrors(failure.fieldErrors);
        return;
      }
      const body: unknown = await response.json();
      if (!isRecord(body) || !Array.isArray(body.items) || !body.items.every(isCase)) {
        setImportError(t0602("errMalformedImport", locale));
        return;
      }
      const createdItems = body.items as EvaluationCase[];
      setImportText("");
      setNotice(
        t0602("noticeImported", locale, {
          count: createdItems.length,
          plural: createdItems.length === 1 ? "" : "s",
        }),
      );
      setCases((previous) =>
        previous === null
          ? previous
          : [...previous, ...createdItems].sort((a, b) => (a.case_key < b.case_key ? -1 : 1)),
      );
    } catch (caught) {
      // Nothing is shown as imported unless the route genuinely answered 201.
      setImportError(networkFailure(caught, locale).message);
    } finally {
      setImporting(false);
    }
  }

  function startEdit(item: EvaluationCase) {
    setEditing(item);
    setDraft(draftFrom(item));
    setEditError(null);
    setEditFieldErrors([]);
    setJsonBlurErrors({});
    setNotice(null);
  }

  function updateDraft(change: Partial<Draft>) {
    setDraft((previous) => (previous === null ? previous : { ...previous, ...change }));
  }

  /** H5: validate a JSON field as soon as it loses focus, not only at submit time. */
  function blurJsonField(
    key: "input_json" | "expected_json" | "grader_config_json",
    value: string,
  ) {
    const valid = parseObjectField(value) !== null;
    setJsonBlurErrors((previous) => {
      if (valid) {
        if (!(key in previous)) return previous;
        const next = { ...previous };
        delete next[key];
        return next;
      }
      return { ...previous, [key]: t0602("jsonInlineInvalid", locale) };
    });
  }

  async function handleSave() {
    if (editing === null || draft === null) return;
    setEditError(null);
    setEditFieldErrors([]);
    const built = buildPatch(editing, draft, locale);
    if ("error" in built) {
      setEditError(built.error);
      return;
    }
    if (Object.keys(built.patch).length === 0) {
      setEditError(t0602("nothingChanged", locale));
      return;
    }
    const effectiveCritical = (built.patch.critical ?? editing.critical) === true;
    const effectiveGrader = String(built.patch.grader_type ?? editing.grader_type);
    if (effectiveCritical && !INDEPENDENT_CRITICAL_GRADERS.includes(effectiveGrader)) {
      setEditError(t0602("criticalBlockedEdit", locale));
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
        const failure = await readFailure(response, locale);
        if (failure.restricted) restrictContent();
        setEditError(failure.message);
        setEditFieldErrors(failure.fieldErrors);
        return;
      }
      const body: unknown = await response.json();
      if (!isCase(body)) {
        setEditError(t0602("errMalformedSave", locale));
        return;
      }
      setCases((previous) =>
        previous === null ? previous : previous.map((item) => (item.id === body.id ? body : item)),
      );
      setEditing(body);
      setDraft(draftFrom(body));
      setNotice(t0602("noticeSaved", locale, { key: body.case_key }));
    } catch (caught) {
      setEditError(networkFailure(caught, locale).message);
    } finally {
      setSaving(false);
    }
  }

  function renderFieldErrors(
    errors: FieldError[],
    testId: string,
    rows: Record<string, unknown>[],
  ) {
    if (errors.length === 0) return null;
    return (
      <ul data-testid={testId} id={testId}>
        {errors.map((entry, position) => {
          const caseKey =
            entry.index !== null && typeof rows[entry.index]?.case_key === "string"
              ? (rows[entry.index]!.case_key as string)
              : null;
          return (
            <li key={position} id={`${testId}-item-${position}`}>
              {rowOrRequest(entry.index, locale)}
              {entry.field === null ? "" : ` / ${entry.field}`}: {entry.code}
              {caseKey ? t0602("rowHint", locale, { key: caseKey }) : null}
            </li>
          );
        })}
      </ul>
    );
  }

  /** editFieldErrors grouped by the exact field name, so each input can be described by its own
   * error (X3) in addition to the flat `edit-field-errors` list kept for the certified suite. */
  const editErrorsByField: Record<string, string[]> = {};
  for (const e of editFieldErrors) {
    const key = e.field ?? "__request__";
    (editErrorsByField[key] ??= []).push(e.code);
  }

  function fieldError(name: string): string | undefined {
    const codes = editErrorsByField[name];
    return codes && codes.length > 0 ? codes.join(", ") : undefined;
  }

  /** Label + input wrapper replicating `ui/Field`'s own id/helper/error/aria-describedby
   * mechanism for the inputs `Field` cannot render (`<select>`, `<textarea>`) -- see the module
   * docstring's X1 disclosure. */
  function describedBy(...ids: (string | undefined)[]): string | undefined {
    return ids.filter(Boolean).join(" ") || undefined;
  }

  return (
    <section aria-label={t0602("heading", locale)} lang={lang} dir={dir}>
      <h2>{t0602("heading", locale)}</h2>

      <div aria-busy={listLoading}>
        <Button
          data-testid="load-cases-button"
          onClick={() => loadPage(null)}
          disabled={listLoading}
        >
          {t0602("loadButton", locale)}
        </Button>
        {listLoading ? <Pending label={t0602("loadingCases", locale)} locale={lang} /> : null}
        {listError ? (
          <p role="alert" data-testid="list-error">
            {listError}
          </p>
        ) : null}
        {restricted ? (
          <p data-testid="restricted-notice">{t0602("restrictedNotice", locale)}</p>
        ) : null}
        {cases !== null ? (
          cases.length === 0 ? (
            <div data-testid="case-list">
              <EmptyState
                title={t0602("emptyTitle", locale)}
                why={t0602("emptyWhy", locale)}
                action={null}
              />
            </div>
          ) : (
            <ul data-testid="case-list">
              {cases.map((item) => (
                <li key={item.id} data-testid={`case-row-${item.id}`}>
                  <span data-testid={`case-key-${item.id}`}>{item.case_key}</span>{" "}
                  <span>{item.category}</span> <span>{item.severity}</span>
                  {item.critical ? <strong> critical</strong> : null} <span>{item.language}</span>{" "}
                  <span>group {item.group_key}</span> <span>{item.grader_type}</span>{" "}
                  <Button data-testid={`edit-case-${item.id}`} onClick={() => startEdit(item)}>
                    Edit
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : null}
        {nextAfter !== null ? (
          <Button
            data-testid="load-more-button"
            onClick={() => loadPage(nextAfter)}
            disabled={listLoading}
          >
            {t0602("loadMoreButton", locale)}
          </Button>
        ) : null}
      </div>

      <div aria-busy={importing}>
        <h3>{t0602("importHeading", locale)}</h3>
        <p id={`${idPrefix}-import-help`}>
          {t0602("importHelp", locale, { max: MAX_IMPORT_CASES })}
        </p>
        <label>
          {t0602("importFileLabel", locale)}
          <input
            type="file"
            accept=".json,.jsonl,application/json"
            data-testid="import-file"
            onChange={(event) => handleFile(event.target.files?.[0])}
          />
        </label>
        <label>
          {t0602("importTextLabel", locale)}
          <textarea
            data-testid="import-text"
            aria-label={t0602("importTextLabel", locale)}
            aria-describedby={describedBy(
              `${idPrefix}-import-help`,
              importFieldErrors.length > 0 ? "import-field-errors" : undefined,
            )}
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
          />
        </label>
        <Button data-testid="import-button" disabled={importing} onClick={handleImport}>
          {t0602("importButton", locale)}
        </Button>
        {importing ? <Pending label={t0602("importingCases", locale)} locale={lang} /> : null}
        {importError ? (
          <p role="alert" data-testid="import-error">
            {importError}
          </p>
        ) : null}
        {renderFieldErrors(importFieldErrors, "import-field-errors", importRows)}
        {notice ? (
          <p role="status" data-testid="notice">
            {notice}
          </p>
        ) : null}
      </div>

      {editing !== null && draft !== null ? (
        <form
          data-testid="case-form"
          aria-busy={saving}
          onSubmit={(event) => {
            event.preventDefault();
            handleSave();
          }}
        >
          <h3>
            {t0602("editHeading", locale)}{" "}
            <span data-testid="edit-case-key">{editing.case_key}</span>
          </h3>
          <label htmlFor="field-category">
            {t0602("fieldCategory", locale)}
            <input
              id="field-category"
              data-testid="field-category"
              value={draft.category}
              onChange={(event) => updateDraft({ category: event.target.value })}
              aria-describedby={describedBy(
                fieldError("category") ? "field-category-error" : undefined,
              )}
            />
          </label>
          {fieldError("category") ? (
            <div id="field-category-error" role="alert">
              {fieldError("category")}
            </div>
          ) : null}
          <label htmlFor="field-severity">
            {t0602("fieldSeverity", locale)}
            <select
              id="field-severity"
              data-testid="field-severity"
              value={draft.severity}
              onChange={(event) => updateDraft({ severity: event.target.value })}
              aria-describedby={describedBy(
                fieldError("severity") ? "field-severity-error" : undefined,
              )}
            >
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {severity}
                </option>
              ))}
            </select>
          </label>
          {fieldError("severity") ? (
            <div id="field-severity-error" role="alert">
              {fieldError("severity")}
            </div>
          ) : null}
          <label htmlFor="field-critical">
            {t0602("fieldCritical", locale)}
            <input
              type="checkbox"
              id="field-critical"
              data-testid="field-critical"
              checked={draft.critical}
              onChange={(event) => updateDraft({ critical: event.target.checked })}
            />
          </label>
          <label htmlFor="field-language">
            {t0602("fieldLanguage", locale)}
            <input
              id="field-language"
              data-testid="field-language"
              value={draft.language}
              onChange={(event) => updateDraft({ language: event.target.value })}
            />
          </label>
          <label htmlFor="field-group-key">
            {t0602("fieldGroupKey", locale)}
            <input
              id="field-group-key"
              data-testid="field-group-key"
              value={draft.group_key}
              onChange={(event) => updateDraft({ group_key: event.target.value })}
              aria-describedby={describedBy(
                fieldError("group_key") ? "field-group-key-error" : undefined,
              )}
            />
          </label>
          {fieldError("group_key") ? (
            <div id="field-group-key-error" role="alert">
              {fieldError("group_key")}
            </div>
          ) : null}
          <label htmlFor="field-grader-type">
            {t0602("fieldGrader", locale)}
            <select
              id="field-grader-type"
              data-testid="field-grader-type"
              value={draft.grader_type}
              onChange={(event) => updateDraft({ grader_type: event.target.value })}
              aria-describedby={describedBy(
                fieldError("grader_type") ? "field-grader-type-error" : undefined,
              )}
            >
              {GRADER_TYPES.map((grader) => (
                <option key={grader} value={grader}>
                  {grader}
                </option>
              ))}
            </select>
          </label>
          {fieldError("grader_type") ? (
            <div id="field-grader-type-error" role="alert">
              {fieldError("grader_type")}
            </div>
          ) : null}
          <label htmlFor="field-input-json">
            {t0602("fieldInputJson", locale)}
            <textarea
              id="field-input-json"
              data-testid="field-input-json"
              value={draft.input_json}
              onChange={(event) => updateDraft({ input_json: event.target.value })}
              onBlur={(event) => blurJsonField("input_json", event.target.value)}
              aria-describedby={describedBy(
                "field-input-json-hint",
                jsonBlurErrors.input_json ? "field-input-json-blur-error" : undefined,
                fieldError("input_json") ? "field-input-json-error" : undefined,
              )}
            />
          </label>
          <div id="field-input-json-hint" data-testid="field-input-json-hint">
            {t0602("jsonHint", locale)}
          </div>
          {jsonBlurErrors.input_json ? (
            <div
              id="field-input-json-blur-error"
              role="alert"
              data-testid="field-input-json-blur-error"
            >
              {jsonBlurErrors.input_json}
            </div>
          ) : null}
          {fieldError("input_json") ? (
            <div id="field-input-json-error" role="alert">
              {fieldError("input_json")}
            </div>
          ) : null}
          <label htmlFor="field-expected-json">
            {t0602("fieldExpectedJson", locale)}
            <textarea
              id="field-expected-json"
              data-testid="field-expected-json"
              value={draft.expected_json}
              onChange={(event) => updateDraft({ expected_json: event.target.value })}
              onBlur={(event) => blurJsonField("expected_json", event.target.value)}
              aria-describedby={describedBy(
                "field-expected-json-hint",
                jsonBlurErrors.expected_json ? "field-expected-json-blur-error" : undefined,
                fieldError("expected_json") ? "field-expected-json-error" : undefined,
              )}
            />
          </label>
          <div id="field-expected-json-hint" data-testid="field-expected-json-hint">
            {t0602("jsonHint", locale)}
          </div>
          {jsonBlurErrors.expected_json ? (
            <div
              id="field-expected-json-blur-error"
              role="alert"
              data-testid="field-expected-json-blur-error"
            >
              {jsonBlurErrors.expected_json}
            </div>
          ) : null}
          {fieldError("expected_json") ? (
            <div id="field-expected-json-error" role="alert">
              {fieldError("expected_json")}
            </div>
          ) : null}
          <label htmlFor="field-grader-config-json">
            {t0602("fieldGraderConfigJson", locale)}
            <textarea
              id="field-grader-config-json"
              data-testid="field-grader-config-json"
              value={draft.grader_config_json}
              onChange={(event) => updateDraft({ grader_config_json: event.target.value })}
              onBlur={(event) => blurJsonField("grader_config_json", event.target.value)}
              aria-describedby={describedBy(
                "field-grader-config-json-hint",
                jsonBlurErrors.grader_config_json
                  ? "field-grader-config-json-blur-error"
                  : undefined,
                fieldError("grader_config_json") ? "field-grader-config-json-error" : undefined,
              )}
            />
          </label>
          <div id="field-grader-config-json-hint" data-testid="field-grader-config-json-hint">
            {t0602("jsonHint", locale)}
          </div>
          {jsonBlurErrors.grader_config_json ? (
            <div
              id="field-grader-config-json-blur-error"
              role="alert"
              data-testid="field-grader-config-json-blur-error"
            >
              {jsonBlurErrors.grader_config_json}
            </div>
          ) : null}
          {fieldError("grader_config_json") ? (
            <div id="field-grader-config-json-error" role="alert">
              {fieldError("grader_config_json")}
            </div>
          ) : null}
          <label htmlFor="field-weight">
            {t0602("fieldWeight", locale)}
            <input
              id="field-weight"
              data-testid="field-weight"
              value={draft.weight}
              onChange={(event) => updateDraft({ weight: event.target.value })}
              aria-describedby={describedBy(
                fieldError("weight") ? "field-weight-error" : undefined,
              )}
            />
          </label>
          {fieldError("weight") ? (
            <div id="field-weight-error" role="alert">
              {fieldError("weight")}
            </div>
          ) : null}
          <Button variant="primary" data-testid="save-case-button" disabled={saving} type="submit">
            {t0602("saveButton", locale)}
          </Button>
          {saving ? <Pending label={t0602("savingCase", locale)} locale={lang} /> : null}
          {editError ? (
            <p role="alert" data-testid="edit-error">
              {editError}
            </p>
          ) : null}
          {renderFieldErrors(editFieldErrors, "edit-field-errors", [])}
        </form>
      ) : null}
    </section>
  );
}
