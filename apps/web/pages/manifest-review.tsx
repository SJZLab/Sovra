import type { ReactElement } from "react";

import { ManifestReviewEditUI } from "../components/target-application/manifest_review_edit_ui_with_per_field_evidence";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "manifest-review")!;

export default function ManifestReviewPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <ManifestReviewEditUI
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          manifestId={v("manifestId")}
        />
      )}
    />
  );
}
