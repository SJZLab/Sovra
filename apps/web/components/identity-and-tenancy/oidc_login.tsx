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

export interface OidcLoginButtonProps {
  apiBaseUrl: string;
  label?: string;
}

export function loginUrl(apiBaseUrl: string): string {
  return `${apiBaseUrl.replace(/\/$/, "")}/auth/login`;
}

export function OidcLoginButton({ apiBaseUrl, label = "Sign in" }: OidcLoginButtonProps) {
  return (
    <a href={loginUrl(apiBaseUrl)} data-testid="oidc-login-link">
      {label}
    </a>
  );
}
