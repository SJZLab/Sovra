import { useEffect, useRef } from "react";

/**
 * A control that disables itself while its request is in flight loses keyboard focus to `<body>`
 * (Chromium blurs a disabled element), on success and on failure alike. `arm()` is called at the
 * start of the handler; when `busy` returns to false, focus goes back to the control that had it,
 * or, if that control was unmounted meanwhile, to `fallback()`. It never steals focus: nothing
 * happens unless focus is on `<body>` at that moment.
 */
export function useRefocusAfter(busy: boolean) {
  const target = useRef<HTMLElement | null>(null);
  const fallback = useRef<(() => HTMLElement | null) | null>(null);
  useEffect(() => {
    if (busy || !target.current) return;
    const armed = target.current;
    const backup = fallback.current;
    target.current = null;
    fallback.current = null;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    (armed.isConnected ? armed : backup?.())?.focus();
  }, [busy]);
  return (backupFocus?: () => HTMLElement | null) => {
    const active = document.activeElement;
    target.current = active instanceof HTMLElement && active !== document.body ? active : null;
    fallback.current = backupFocus ?? null;
  };
}
