import Head from "next/head";
import type { ReactElement } from "react";

import { OidcLoginButton } from "../components/identity-and-tenancy/oidc_login";
import { JOURNEYS } from "../lib/journeys";

// Front door (CLAUDE.md Law 6): "/" is where a user starts. Each journey is a form here.
export default function Home(): ReactElement {
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  return (
    <main>
      <Head>
        <title>Sovra Factory</title>
      </Head>
      <h1>Sovra Factory</h1>
      <section>
        <h2>Sign in</h2>
        {apiBaseUrl ? (
          <OidcLoginButton apiBaseUrl={apiBaseUrl} />
        ) : (
          <p role="alert">NEXT_PUBLIC_API_BASE_URL is not set, so sign-in cannot reach the API.</p>
        )}
      </section>
      {JOURNEYS.map((j) => (
        <section key={j.slug}>
          <h2>{j.title}</h2>
          <form action={`/${j.slug}`} method="get">
            {j.params.map((p) => (
              <label key={p.name}>
                {p.label}
                <input name={p.name} required />
              </label>
            ))}
            <button type="submit">Open</button>
          </form>
        </section>
      ))}
    </main>
  );
}
