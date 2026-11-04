import Head from "next/head";
import { useRouter } from "next/router";
import type { ReactElement } from "react";

import { ModelToArtifactMappingUI } from "../components/artifact-capabilities/model_to_artifact_mapping_ui";

// Journey page: mounts the real mapping component with the values from the front door form.
// The API base URL must be configured; a missing value is shown, never silently defaulted.
export default function ArtifactMappingPage(): ReactElement {
  const router = useRouter();
  if (!router.isReady) {
    return <p>Loading…</p>;
  }
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const organizationId = String(router.query.organizationId ?? "");
  const connectionId = String(router.query.connectionId ?? "");
  const modelName = String(router.query.modelName ?? "");

  let body: ReactElement;
  if (!apiBaseUrl) {
    body = <p role="alert">NEXT_PUBLIC_API_BASE_URL is not set, so the API cannot be reached.</p>;
  } else if (!organizationId || !connectionId || !modelName) {
    body = (
      <p role="alert">
        Missing organization, connection or model. <a href="/">Start again</a>.
      </p>
    );
  } else {
    body = (
      <ModelToArtifactMappingUI
        apiBaseUrl={apiBaseUrl}
        organizationId={organizationId}
        connectionId={connectionId}
        modelName={modelName}
      />
    );
  }
  return (
    <main>
      <Head>
        <title>Artifact mapping · Sovra Factory</title>
      </Head>
      {body}
    </main>
  );
}
