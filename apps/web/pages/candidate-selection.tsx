import type { ReactElement } from "react";

import { CandidateSelectionUI } from "../components/target-application/candidate_selection_ui_no_auto_selection";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "candidate-selection")!;

export default function CandidateSelectionPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <CandidateSelectionUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          connectionId={v("connectionId")}
          snapshotId={v("snapshotId")}
        />
      )}
    />
  );
}
