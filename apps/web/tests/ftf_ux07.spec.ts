// @vitest-environment jsdom
//
// FTF-UX07 -- OIDC login redesign (audit-2026-10.md section 1, H10 sev 1).
// The certified ftf_0101.spec.ts stays unchanged; this file pins what UX07 adds.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { OidcLoginButton } from "../components/identity-and-tenancy/oidc_login";

afterEach(cleanup);

const API = "http://localhost:8000";

test("UX07-H10: the link is described by a hint naming the identity provider and the return", () => {
  render(OidcLoginButton({ apiBaseUrl: API }));
  const link = screen.getByTestId("oidc-login-link");
  const hint = screen.getByTestId("oidc-login-hint");
  expect(link.getAttribute("aria-describedby")).toBe(hint.id);
  expect(hint.textContent).toBe(
    "You will be sent to your organization's identity provider to sign in, then returned here.",
  );
});

test("UX07-AR: Arabic locale renders exact Arabic copy with lang and dir=rtl", () => {
  render(OidcLoginButton({ apiBaseUrl: API, locale: "ar" }));
  const root = screen.getByTestId("oidc-login");
  expect(root.getAttribute("lang")).toBe("ar");
  expect(root.getAttribute("dir")).toBe("rtl");
  expect(screen.getByTestId("oidc-login-link").textContent).toBe("تسجيل الدخول");
  expect(screen.getByTestId("oidc-login-hint").textContent).toBe(
    "ستنتقل إلى مزود الهوية الخاص بمؤسستك لتسجيل الدخول، ثم تعود إلى هنا.",
  );
});

test("UX07-NEG: an unshipped locale renders English with lang=en, not the echoed value", () => {
  render(OidcLoginButton({ apiBaseUrl: API, locale: "fr" }));
  const root = screen.getByTestId("oidc-login");
  expect(root.getAttribute("lang")).toBe("en");
  expect(root.getAttribute("dir")).toBe("ltr");
  expect(screen.getByTestId("oidc-login-link").textContent).toBe("Sign in");
});

test("UX07-NEG: a prototype-key locale does not break the catalogue", () => {
  render(OidcLoginButton({ apiBaseUrl: API, locale: "__proto__" }));
  expect(screen.getByTestId("oidc-login-link").textContent).toBe("Sign in");
});

test("UX07-H5: the control stays a real top-level link with no JS handler, at the target size", () => {
  render(OidcLoginButton({ apiBaseUrl: API }));
  const link = screen.getByTestId("oidc-login-link") as HTMLAnchorElement;
  expect(link.tagName).toBe("A");
  expect(link.getAttribute("href")).toBe(`${API}/auth/login`);
  expect(link.onclick).toBeNull();
  expect(link.style.minBlockSize).toBe("var(--target-min)");
  expect(link.style.background).toBe("var(--color-action)");
});

test("UX07: a custom label and hintId are honoured", () => {
  render(OidcLoginButton({ apiBaseUrl: API, label: "Sign in with SSO", hintId: "h1" }));
  expect(screen.getByTestId("oidc-login-link").textContent).toBe("Sign in with SSO");
  expect(screen.getByTestId("oidc-login-hint").id).toBe("h1");
});
