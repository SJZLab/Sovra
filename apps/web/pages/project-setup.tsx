import type { ReactElement } from "react";

import { ProjectSetupForm } from "../components/project-mandate/project_schema_api_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "project-setup")!;

export default function ProjectSetupPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <ProjectSetupForm
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          workspaceId={v("workspaceId")}
        />
      )}
    />
  );
}
