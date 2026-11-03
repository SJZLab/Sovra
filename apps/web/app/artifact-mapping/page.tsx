"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { ModelToArtifactMappingUI } from "../../components/artifact-capabilities/model_to_artifact_mapping_ui";

// Journey page: mounts the real mapping component with the values from the front door form.
// The API base URL must be configured; a missing value is shown, never silently defaulted.
function MappingJourney() {
  const params = useSearchParams();
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  const organizationId = params.get("organizationId") ?? "";
  const connectionId = params.get("connectionId") ?? "";
  const modelName = params.get("modelName") ?? "";

  if (!apiBaseUrl) {
    return <p role="alert">NEXT_PUBLIC_API_BASE_URL is not set, so the API cannot be reached.</p>;
  }
  if (!organizationId || !connectionId || !modelName) {
    return (
      <p role="alert">
        Missing organization, connection or model. <a href="/">Start again</a>.
      </p>
    );
  }
  return (
    <ModelToArtifactMappingUI
      apiBaseUrl={apiBaseUrl}
      organizationId={organizationId}
      connectionId={connectionId}
      modelName={modelName}
    />
  );
}

export default function ArtifactMappingPage() {
  return (
    <Suspense fallback={<p>Loading…</p>}>
      <MappingJourney />
    </Suspense>
  );
}
