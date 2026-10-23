import { useEffect, useState } from "react";

import { dirFor, phrase, resolveLocale, type Locale } from "./phrases";

/**
 * NN/g response-time limits: nothing before 1 s (direct manipulation feedback is the caller's
 * 0.1 s job), a busy indicator after 1 s, and a determinate/cancellable progress indicator after 10 s.
 */
export const BUSY_AFTER_MS = 1000;
export const PROGRESS_AFTER_MS = 10000;

export function phaseFor(elapsedMs: number): "none" | "busy" | "progress" {
  if (elapsedMs >= PROGRESS_AFTER_MS) return "progress";
  if (elapsedMs >= BUSY_AFTER_MS) return "busy";
  return "none";
}

export interface PendingViewProps {
  phase: "none" | "busy" | "progress";
  label: string;
  onCancel?: () => void;
  value?: number;
  locale?: Locale;
}

/**
 * Pure presentational half of `Pending`, split out so a test (or the real-browser evidence
 * harness) can render any phase directly without waiting on real timers.
 */
export function PendingView({ phase, label, onCancel, value, locale = "en" }: PendingViewProps) {
  if (phase === "none") return null;
  const determinate = typeof value === "number" && Number.isFinite(value);
  const percent = determinate ? Math.min(100, Math.max(0, value as number)) : 0;
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
    >
      <span>{label}</span>
      {phase === "progress" ? (
        <>
          <div
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={determinate ? percent : undefined}
            data-testid="progress-track"
            style={{
              boxSizing: "border-box",
              blockSize: "8px",
              inlineSize: "100%",
              background: "var(--color-surface-2)",
              border: "1px solid var(--color-muted)",
            }}
          >
            <div
              data-testid="progress-fill"
              data-indeterminate={determinate ? undefined : "true"}
              style={{
                blockSize: "100%",
                inlineSize: determinate ? `${percent}%` : "100%",
                // Indeterminate: stripes, so the bar never reads as a finished 100%.
                background: determinate
                  ? "var(--color-action)"
                  : "repeating-linear-gradient(45deg, var(--color-action) 0 8px, var(--color-surface-2) 8px 16px)",
              }}
            />
          </div>
          <span>{determinate ? `${Math.round(percent)}%` : phrase("working", locale)}</span>
          {onCancel ? (
            <button
              type="button"
              onClick={onCancel}
              style={{ minBlockSize: "var(--target-min)", minInlineSize: "var(--target-min)" }}
            >
              {phrase("cancel", locale)}
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function Pending({
  label,
  onCancel,
  value,
  locale = "en",
}: {
  label: string;
  onCancel?: () => void;
  value?: number;
  locale?: Locale;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - t0), 250);
    return () => clearInterval(id);
  }, []);
  const phase = phaseFor(elapsed);
  return (
    <PendingView phase={phase} label={label} onCancel={onCancel} value={value} locale={locale} />
  );
}
