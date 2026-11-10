import type { ReactElement } from "react";

import { ModelToArtifactMappingUI } from "../components/artifact-capabilities/model_to_artifact_mapping_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "artifact-mapping")!;

export default function ArtifactMappingPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <ModelToArtifactMappingUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          connectionId={v("connectionId")}
          modelName={v("modelName")}
        />
      )}
    />
  );
}
