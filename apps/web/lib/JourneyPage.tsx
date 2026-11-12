import Head from "next/head";
import { useRouter } from "next/router";
import type { ReactElement } from "react";

import type { Journey } from "./journeys";

export type JourneyValue = (name: string) => string;

// Shared journey shell: reads the journey's query parameters, refuses to render without the API base
// URL or a required value (shown, never silently defaulted), and mounts the real component.
export function JourneyPage({
  journey,
  render,
}: {
  journey: Journey;
  render: (value: JourneyValue, apiBaseUrl: string) => ReactElement;
}): ReactElement {
  const router = useRouter();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  let body: ReactElement;
  if (!router.isReady) {
    body = <p>Loading…</p>;
  } else if (!apiBaseUrl) {
    body = <p role="alert">NEXT_PUBLIC_API_BASE_URL is not set, so the API cannot be reached.</p>;
  } else {
    const values = new Map<string, string>();
    const missing: string[] = [];
    for (const p of journey.params) {
      const raw = router.query[p.name];
      const v = typeof raw === "string" ? raw : Array.isArray(raw) ? (raw[0] ?? "") : "";
      values.set(p.name, v);
      if (!v) missing.push(p.label);
    }
    const value: JourneyValue = (name) => values.get(name) ?? "";
    body =
      missing.length > 0 ? (
        <p role="alert">
          Missing {missing.join(", ")}. <a href="/">Start again</a>.
        </p>
      ) : (
        render(value, apiBaseUrl)
      );
  }
  return (
    <main>
      <Head>
        <title>{journey.title} · Sovra Factory</title>
      </Head>
      {body}
    </main>
  );
}
