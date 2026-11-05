import type { ReactElement } from "react";

import { ModelCatalogUI } from "../components/litellm-gateway/model_catalog_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "model-catalog")!;

export default function ModelCatalogPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <ModelCatalogUI apiBaseUrl={apiBaseUrl} organizationId={v("organizationId")} connectionId={v("connectionId")} />
      )}
    />
  );
}
