import type { ReactElement } from "react";

import { RequirementsPanel } from "../components/project-mandate/requirements_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "requirements")!;

export default function RequirementsPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <RequirementsPanel
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          securityProfile={v("securityProfile")}
        />
      )}
    />
  );
}
