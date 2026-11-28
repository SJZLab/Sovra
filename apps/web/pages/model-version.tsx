import type { ReactElement } from "react";

import { ModelVersionPanel } from "../components/model-import/model_version_panel_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "model-version")!;

// FTF-H010-P6: the model version panel (lineage, roles, capability-driven actions), reached from "/".
export default function ModelVersionPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <ModelVersionPanel
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          modelVersionId={v("modelVersionId")}
        />
      )}
    />
  );
}
