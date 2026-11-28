import type { ReactElement } from "react";

import { DatasetSourceSelectorPurposeAuthorizationUi } from "../components/dataset-factory/dataset_source_selector_purpose_authorization_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "dataset-source")!;

export default function DatasetSourcePage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <DatasetSourceSelectorPurposeAuthorizationUi
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
        />
      )}
    />
  );
}
