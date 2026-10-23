import { useRef } from "react";

/**
 * A second activation while the first request is still in flight (a double click, Enter held down)
 * must not send a second request -- for a write that records a rate twice or runs a paid probe
 * twice. `begin()` returns false when one is already running; call `end()` when it settles.
 */
export function useSingleFlight() {
  const running = useRef(false);
  return {
    begin(): boolean {
      if (running.current) return false;
      running.current = true;
      return true;
    },
    end(): void {
      running.current = false;
    },
  };
}
