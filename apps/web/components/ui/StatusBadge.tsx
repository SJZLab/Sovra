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

const WORD: Record<StatusState, string> = {
  draft: "Draft",
  not_connected: "Not connected",
  validation_required: "Validation required",
  blocked: "Blocked",
  queued: "Queued",
  running: "Running",
  failed: "Failed",
  cancelled: "Cancelled",
  qualified: "Qualified for",
  production: "Production",
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
}

export function statusLabel({ state, detail }: StatusBadgeProps): string {
  if (state === "blocked" || state === "qualified") {
    const d = detail?.trim();
    if (!d) throw new Error(`StatusBadge ${state} requires a non-blank detail (section 72.2)`);
    return state === "blocked" ? `${WORD.blocked} — ${d}` : `${WORD.qualified} ${d}`;
  }
  return WORD[state];
}

export function StatusBadge(props: StatusBadgeProps) {
  const label = statusLabel(props);
  return (
    <span
      data-status={props.state}
      data-tone={TONE[props.state]}
      style={{
        color: `var(--color-${TONE[props.state] === "muted" ? "muted" : TONE[props.state]})`,
      }}
    >
      <span aria-hidden="true">{ICON[props.state]}</span> {label}
    </span>
  );
}
