import type { ReactElement } from "react";

import { DatasetIssueResolutionUI } from "../components/dataset-factory/dataset_issue_resolution_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "dataset-issues")!;

export default function DatasetIssuesPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <DatasetIssueResolutionUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          datasetId={v("datasetId")}
        />
      )}
    />
  );
}
