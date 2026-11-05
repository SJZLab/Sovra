import Head from "next/head";
import type { ReactElement } from "react";

// Front door (CLAUDE.md Law 6): "/" is where a user starts. Each journey is reached from here.
export default function Home(): ReactElement {
  return (
    <main>
      <Head>
        <title>Sovra Factory</title>
      </Head>
      <h1>Sovra Factory</h1>
      <h2>Map a model to a trainable artifact</h2>
      <form action="/artifact-mapping" method="get">
        <label>
          Organization ID
          <input name="organizationId" required />
        </label>
        <label>
          Connection ID
          <input name="connectionId" required />
        </label>
        <label>
          Model name
          <input name="modelName" required />
        </label>
        <button type="submit">Open mapping</button>
      </form>
    </main>
  );
}
