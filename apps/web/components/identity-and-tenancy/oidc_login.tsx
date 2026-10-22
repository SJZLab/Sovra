/**
 * OIDC login entry point (FTF-0101).
 *
 * A real top-level navigation `<a>`, not a `fetch`/`onClick` JS redirect:
 * the OAuth authorization-code flow requires the *browser* to navigate to
 * the provider (so it can set/validate cookies and eventually redirect
 * back), which a same-origin XHR/fetch cannot do. `apiBaseUrl` is passed
 * in rather than hard-coded so the same component works against any
 * deployed Factory API origin.
 *
 * Scope disclosed, not hidden (see docs/certifications/queue.md): this
 * repository has no Next.js app shell yet (no `next.config`, no
 * `app/page.tsx`) -- FTF-0101's own file list names only this component
 * and its test, not a full page/routing scaffold. Mounting this component
 * into a real page is a follow-up for whichever task first builds
 * `apps/web`'s app shell, matching the precedent FTF-0004 set by
 * excluding `apps/web`'s server runtime from its own scope.
 */

import type { CSSProperties } from "react";

import { dirFor, resolveLocale } from "../ui/phrases";
import { loginString } from "./strings_0101";

export interface OidcLoginButtonProps {
  apiBaseUrl: string;
  label?: string;
  /** "ar" renders Arabic copy with `dir="rtl"`; any other value renders English. */
  locale?: string;
  /** Id of the hint paragraph the link points at via aria-describedby. */
  hintId?: string;
}

export function loginUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/$/, "")}/auth/login`;
}

/** Link styled as the primary action: the §72.1 action tokens, the minimum target size, and a
 * text-decoration-free fill that is distinct from body text by fill and border, not hue alone. */
const LINK_STYLE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  minBlockSize: "var(--target-min)",
  minInlineSize: "var(--target-min)",
  paddingInline: "1rem",
  background: "var(--color-action)",
  color: "var(--color-action-text)",
  border: "2px solid var(--color-action)",
  textDecoration: "none",
};

export function OidcLoginButton({
  apiBaseUrl,
  label,
  locale = "en",
  hintId = "oidc-login-hint",
}: OidcLoginButtonProps) {
  const lang = resolveLocale(locale);
  return (
    <div lang={lang} dir={dirFor(lang)} data-testid="oidc-login">
      {/* Still a real top-level <a> (never a JS redirect): H5/Law 17. */}
      <a
        href={loginUrl(apiBaseUrl)}
        data-testid="oidc-login-link"
        aria-describedby={hintId}
        style={LINK_STYLE}
      >
        {label ?? loginString("signIn", lang)}
      </a>
      {/* H10: say where the click leads and that the user comes back. */}
      <p id={hintId} data-testid="oidc-login-hint">
        {loginString("hint", lang)}
      </p>
    </div>
  );
}
