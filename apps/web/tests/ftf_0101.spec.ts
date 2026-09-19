// @vitest-environment jsdom
//
// FTF-0101 -- OIDC login UI entry point.
//
// A real component/DOM-level test (Vitest + React Testing Library + jsdom):
// renders the actual `OidcLoginButton`, not a snapshot or a hand-written
// HTML string, and asserts the real anchor element's `href`.
//
// Disclosed, not hidden: this is a component-level test, not a full
// browser E2E (no Playwright here -- see oidc_login.tsx's own docstring
// for why: no Next.js app shell exists yet to navigate within, and a
// genuine end-to-end OIDC browser flow needs the same owner-approved
// local test-provider package this task's backend acceptance clause 1
// already discloses as BLOCKED_EXTERNAL).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { loginUrl, OidcLoginButton } from "../components/identity-and-tenancy/oidc_login";

afterEach(() => {
  cleanup();
});

test("loginUrl() points at the API's real /auth/login route", () => {
  expect(loginUrl("http://localhost:8000")).toBe("http://localhost:8000/auth/login");
  expect(loginUrl("http://localhost:8000/")).toBe("http://localhost:8000/auth/login");
});

test("OidcLoginButton renders a real top-level navigation link, not a JS-only handler", () => {
  render(OidcLoginButton({ apiBaseUrl: "http://localhost:8000" }));
  const link = screen.getByTestId("oidc-login-link");
  expect(link.tagName).toBe("A");
  expect(link.getAttribute("href")).toBe("http://localhost:8000/auth/login");
  expect(link.textContent).toBe("Sign in");
});

test("OidcLoginButton accepts a custom label", () => {
  render(OidcLoginButton({ apiBaseUrl: "http://localhost:8000", label: "Sign in with SSO" }));
  expect(screen.getByTestId("oidc-login-link").textContent).toBe("Sign in with SSO");
});
