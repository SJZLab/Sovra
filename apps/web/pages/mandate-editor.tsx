import type { ReactElement } from "react";

import { MandateEditorAndConflictResolutionUI } from "../components/target-application/mandate_editor_and_conflict_resolution_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "mandate-editor")!;

export default function MandateEditorPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <MandateEditorAndConflictResolutionUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          manifestId={v("manifestId")}
        />
      )}
    />
  );
}
