// FTF-UX01-F1 round 2: exposes the shared error helpers to a real Chromium page, so the browser's
// own DOMException (an `Error` in Chromium, not in jsdom) can be passed through `networkError`.
import { networkError } from "../../components/ui/errors";
import { phrase } from "../../components/ui/phrases";

(window as unknown as { __ux01f1: unknown }).__ux01f1 = { networkError, phrase };
