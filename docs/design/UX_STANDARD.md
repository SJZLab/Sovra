# Sovra UX standard (mandatory for every UI change)

Owner instruction, live 2026-10-16: every Sovra screen is designed with the vendored **ui-ux-pro-max**
skill (`.claude/skills/ui-ux-pro-max/`) and must follow **Nielsen Norman Group (NN/g)** best practice
as a hard requirement. This document turns that into checkable DoD items. It sits under
FINE_TUNING_FACTORY_RELEASE_1.md §72 (visual system, honest interaction contract, UI acceptance) and
§22 (screens) and never relaxes them; where this document and §72 both speak, the stricter one wins.

## 1. Fixed inputs (not redesigned)

- Visual system: §72.1 exactly: a dense, neutral AI-operations console; the token values in
  `apps/web/styles/tokens.css` (spacing 4/8/12/16/24/32 px; body 14/20 px; table 13 px; label 12 px;
  page title 24 px; surfaces #FFFFFF/#F8FAFC; border #CBD5E1; text #0F172A; muted #475569; action
  #1D4ED8; success #166534; warning #92400E; danger #B91C1C); separately tested dark-mode tokens;
  local/system fonts only (no CDN in ZERO_EGRESS).
- Components: shared accessible components on Radix primitives / shadcn-style patterns (§72.1).
- Accessibility target: WCAG 2.2 AA on the implemented screens (§72.1, S16).
- Copy: standard system phrases in both languages (English and Arabic, RTL) without asking; brand,
  hero, marketing, pricing, legal and customer-email copy only with owner approval (CLAUDE.md).

The skill's `--design-system` generator is therefore NOT used to choose a palette, font or style.

## 2. How the skill is used on each UI slice

1. `python3 "$CLAUDE_PROJECT_DIR/.claude/skills/ui-ux-pro-max/scripts/search.py" "<one intent>" --domain ux`
   for each interaction the screen has (forms, errors, tables, dialogs, navigation, loading,
   empty states, destructive actions, long-running jobs), plus `--stack react` (and `--stack nextjs` /
   `--stack shadcn` once the §72.1 shell exists) and `--domain chart` for any chart.
2. Record the queries and the guideline IDs applied in the slice's submission doc (a 0-result query is
   recorded as such, never invented - the skill's own rule).
3. Run the skill's pre-delivery checklist (`references/pro-rules.md`, `references/quick-reference.md`
   priorities 1-10) and record each item as met / not applicable (with reason).

## 3. NN/g requirements

### 3.1 The 10 usability heuristics (Jakob Nielsen, NN/g)

Every screen is evaluated against all ten; each needs at least one concrete, testable check in the
slice's tests or review record.

| # | Heuristic | What it means for Sovra screens |
|---|-----------|----------------------------------|
| H1 | Visibility of system status | Every job, import, evaluation, deploy shows its real state (§72.2 state words: Draft, Not connected, Validation required, Blocked — reason, Queued, Running, Failed, Cancelled, Qualified for [scope], Production) within 1 s of a change; long operations show progress or an honest "pending"; never a fabricated success (Law 17). |
| H2 | Match between system and the real world | Domain words the user knows (dataset purpose, held-out, candidate, release); no internal codes or table names in the UI; dates, numbers and units localised (en/ar). |
| H3 | User control and freedom | Cancel and Back on every multi-step flow; undo or a confirm step for destructive actions; drafts survive refresh, back/forward and connection loss (§44, §72.2). |
| H4 | Consistency and standards | One component per pattern (shared library); the same action has the same name and place on every screen; platform conventions (links look like links, buttons like buttons). |
| H5 | Error prevention | Constrain inputs before submit (typed fields, disabled impossible actions with the reason shown); confirm high-cost or irreversible actions with the specific consequence; no silent defaults that commit data. |
| H6 | Recognition rather than recall | Show the current project/environment banner, breadcrumbs and the selected IDs/versions; never ask the user to remember a value from another screen. |
| H7 | Flexibility and efficiency of use | Keyboard operation for every action; sensible defaults that are visible and editable; bulk actions on tables; deep links to every view. |
| H8 | Aesthetic and minimalist design | Only information that serves the current decision; progressive disclosure for detail (evidence drawers); dense but uncluttered per §72.1. |
| H9 | Help users recognize, diagnose and recover from errors | Plain-language error, next to the field and in a focusable error summary; says what happened, why, and what to do next; maps every API error code (401/403/404/409/422/quota/network) to a message and a recovery action. |
| H10 | Help and documentation | Inline helper text where a field is non-obvious; a link to the relevant doc for complex concepts (split roles, qualification); empty states explain what to do first. |

### 3.2 NN/g practice rules

- **Forms** (NN/g form-design guidance): visible labels above fields, never placeholder-only; mark
  optional (or required) consistently; inline validation after the field loses focus, not on every
  keystroke; group related fields; one column; a primary action that names the result ("Create
  dataset", not "Submit").
- **Error messages**: visible, near the source, in plain language, constructive, never blaming,
  preserving the user's input.
- **Empty states**: explain why it is empty and give the first action; the default overview is empty,
  never a fabricated passing model (§72.2).
- **Tables and data**: sortable/filterable where lists exceed one screen, column headers that say the
  unit, responsive summary/details on mobile (§72.1).
- **Response times** (NN/g 0.1 s / 1 s / 10 s limits): feedback within 0.1 s for direct manipulation,
  a busy indicator after 1 s, a progress indicator with cancel after 10 s.
- **Navigation**: persistent navigation, breadcrumbs, the user's location always visible; Back never
  loses work.
- **Writing for the web**: front-load the key words in headings, buttons and messages; short, scannable
  text.

## 4. Evidence required per UI slice (added to the DoD)

1. The skill queries and the guidelines applied (section 2).
2. A heuristic-evaluation table: H1-H10 x each screen state (empty, loading, populated, error,
   blocked/permission-denied), each cell = met, or a finding with an NN/g severity rating (0 none,
   1 cosmetic, 2 minor, 3 major, 4 catastrophe). Severity 3-4 blocks the slice; 2 needs a recorded
   decision.
3. Behavioural tests for the H1/H3/H5/H9 checks (state shown, cancel/back, prevention, error recovery)
   and the §72.5 viewports (1280/768/390 px), keyboard-only and 200 % zoom.
4. Automated accessibility checks plus the manual WCAG 2.2 AA items (§72.5: a zero-violation scan alone
   is not conformance).
5. The L2 verifier repeats the heuristic evaluation independently (NN/g recommends several
   evaluators; the producer's own evaluation is one of them, never the only one) and reports any
   severity 3-4 finding as NOT CERTIFIED.

## 5. Sources

- NN/g, "10 Usability Heuristics for User Interface Design" - https://www.nngroup.com/articles/ten-usability-heuristics/
- NN/g, "How to Conduct a Heuristic Evaluation" and "Severity Ratings for Usability Problems" - https://www.nngroup.com/articles/how-to-conduct-a-heuristic-evaluation/ , https://www.nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/
- NN/g, "Website Forms Usability" and "Error-Message Guidelines" - https://www.nngroup.com/articles/web-form-design/ , https://www.nngroup.com/articles/error-message-guidelines/
- NN/g, "Response Times: The 3 Important Limits" - https://www.nngroup.com/articles/response-times-3-important-limits/
- W3C WCAG 2.2 (S16) - https://www.w3.org/TR/WCAG22/
