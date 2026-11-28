import type { ReactElement } from "react";

import { CapabilityCompatibilityUI } from "../components/artifact-capabilities/capability_compatibility_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "capability-compatibility")!;

export default function CapabilityCompatibilityPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <CapabilityCompatibilityUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          connectionId={v("connectionId")}
          projectId={v("projectId")}
          modelName={v("modelName")}
        />
      )}
    />
  );
}
