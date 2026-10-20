import { useEffect, useState } from "react";

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

export function Pending({
  label,
  onCancel,
  value,
}: {
  label: string;
  onCancel?: () => void;
  value?: number;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Date.now() - t0), 250);
    return () => clearInterval(id);
  }, []);
  const phase = phaseFor(elapsed);
  if (phase === "none") return null;
  return (
    <div role="status" aria-live="polite">
      <span>{label}</span>
      {phase === "progress" ? (
        <>
          <div
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={value}
          />
          {onCancel ? (
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
