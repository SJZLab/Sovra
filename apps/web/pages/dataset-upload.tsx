import type { ReactElement } from "react";

import { ResumableDatasetUploadUI } from "../components/dataset-factory/resumable_dataset_upload_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "dataset-upload")!;

export default function DatasetUploadPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <ResumableDatasetUploadUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          datasetSourceConnectionId={v("datasetSourceConnectionId")}
        />
      )}
    />
  );
}
