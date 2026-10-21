import { dirFor, phrase, type Locale } from "./phrases";

/** The section 72.2 state words. Status is always text AND icon, never colour alone. */
export type StatusState =
  | "draft"
  | "not_connected"
  | "validation_required"
  | "blocked"
  | "queued"
  | "running"
  | "failed"
  | "cancelled"
  | "qualified"
  | "production";

const WORD_KEY: Record<StatusState, Parameters<typeof phrase>[0]> = {
  draft: "statusDraft",
  not_connected: "statusNotConnected",
  validation_required: "statusValidationRequired",
  blocked: "statusBlocked",
  queued: "statusQueued",
  running: "statusRunning",
  failed: "statusFailed",
  cancelled: "statusCancelled",
  qualified: "statusQualified",
  production: "statusProduction",
};

const ICON: Record<StatusState, string> = {
  draft: "✎",
  not_connected: "⊘",
  validation_required: "!",
  blocked: "⛔",
  queued: "…",
  running: "▶",
  failed: "✕",
  cancelled: "■",
  qualified: "✓",
  production: "★",
};

const TONE: Record<StatusState, "success" | "warning" | "danger" | "muted"> = {
  draft: "muted",
  not_connected: "muted",
  validation_required: "warning",
  blocked: "danger",
  queued: "muted",
  running: "muted",
  failed: "danger",
  cancelled: "muted",
  qualified: "success",
  production: "success",
};

export interface StatusBadgeProps {
  state: StatusState;
  /** Required for `blocked` ("Blocked — reason") and `qualified` ("Qualified for [scope]"). */
  detail?: string;
  locale?: Locale;
}

/**
 * Pure validator for a caller that wants to catch a missing detail before ever rendering
 * (e.g. a form's own submit-time check) — this one still throws. The `StatusBadge` component
 * itself never does (NN/g H9: a render never crashes the page over a missing detail).
 */
export function statusLabel({ state, detail, locale = "en" }: StatusBadgeProps): string {
  if (state === "blocked" || state === "qualified") {
    const d = detail?.trim();
    if (!d) throw new Error(`StatusBadge ${state} requires a non-blank detail (section 72.2)`);
    return state === "blocked"
      ? `${phrase(WORD_KEY.blocked, locale)} — ${d}`
      : `${phrase(WORD_KEY.qualified, locale)} ${d}`;
  }
  return phrase(WORD_KEY[state], locale);
}

/** Same mapping as `statusLabel`, but falls back instead of throwing — used by the component. */
function renderLabel(state: StatusState, detail: string | undefined, locale: Locale): string {
  const d = detail?.trim();
  if (state === "blocked")
    return d
      ? `${phrase("statusBlocked", locale)} — ${d}`
      : phrase("statusBlockedReasonUnavailable", locale);
  if (state === "qualified")
    return d ? `${phrase("statusQualified", locale)} ${d}` : phrase("statusQualified", locale);
  return phrase(WORD_KEY[state], locale);
}

export function StatusBadge({ state, detail, locale = "en" }: StatusBadgeProps) {
  const label = renderLabel(state, detail, locale);
  return (
    <span
      data-status={state}
      data-tone={TONE[state]}
      lang={locale}
      dir={dirFor(locale)}
      style={{
        color: `var(--color-${TONE[state] === "muted" ? "muted" : TONE[state]})`,
      }}
    >
      <span aria-hidden="true">{ICON[state]}</span> {label}
    </span>
  );
}
