import type { ReactElement } from "react";

import { DatasetMappingWizard } from "../components/dataset-factory/dataset_mapping_wizard_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "dataset-mapping")!;

export default function DatasetMappingPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <DatasetMappingWizard
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          datasetId={v("datasetId")}
        />
      )}
    />
  );
}
