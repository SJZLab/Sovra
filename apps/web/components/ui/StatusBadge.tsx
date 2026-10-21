import { dirFor, phrase, type Locale } from "./phrases";
import { cleanText } from "./text";

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
    const d = cleanText(detail);
    if (!d) throw new Error(`StatusBadge ${state} requires a non-blank detail (section 72.2)`);
    return state === "blocked"
      ? `${phrase(WORD_KEY.blocked, locale)} — ${d}`
      : `${phrase(WORD_KEY.qualified, locale)} ${d}`;
  }
  return phrase(WORD_KEY[state], locale);
}

/** Same mapping as `statusLabel`, but falls back instead of throwing — used by the component. */
function renderLabel(state: StatusState, detail: string | undefined, locale: Locale): string {
  const d = cleanText(detail);
  if (!(state in WORD_KEY)) return phrase("statusUnknown", locale);
  if (state === "blocked")
    return d
      ? `${phrase("statusBlocked", locale)} — ${d}`
      : phrase("statusBlockedReasonUnavailable", locale);
  if (state === "qualified")
    return d ? `${phrase("statusQualified", locale)} ${d}` : phrase("statusQualifiedBare", locale);
  return phrase(WORD_KEY[state], locale);
}

/** A state the API sent that this UI does not know yet: a neutral badge, never a crash (H9). */
const UNKNOWN_ICON = "?";

export function StatusBadge({ state, detail, locale = "en" }: StatusBadgeProps) {
  const known = state in WORD_KEY;
  const label = renderLabel(state, detail, locale);
  const tone = known ? TONE[state] : "muted";
  return (
    <span
      data-status={known ? state : "unknown"}
      data-tone={tone}
      lang={locale}
      dir={dirFor(locale)}
      style={{
        color: `var(--color-${tone === "muted" ? "muted" : tone})`,
      }}
    >
      <span aria-hidden="true">{known ? ICON[state] : UNKNOWN_ICON}</span> {label}
    </span>
  );
}
