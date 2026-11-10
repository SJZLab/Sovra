import Head from "next/head";
import type { ReactElement } from "react";

import { JOURNEYS } from "../lib/journeys";

// Front door (CLAUDE.md Law 6): "/" is where a user starts. Each journey is a form here.
export default function Home(): ReactElement {
  return (
    <main>
      <Head>
        <title>Sovra Factory</title>
      </Head>
      <h1>Sovra Factory</h1>
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
