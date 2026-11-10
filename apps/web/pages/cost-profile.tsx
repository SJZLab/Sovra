import type { ReactElement } from "react";

import { CostProfileSearchPanel } from "../components/cost-and-model-search/costprofile_schema_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "cost-profile")!;

export default function CostProfilePage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <CostProfileSearchPanel
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
        />
      )}
    />
  );
}
