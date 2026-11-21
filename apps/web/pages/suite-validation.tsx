import type { ReactElement } from "react";

import { SuiteValidationLocking } from "../components/evaluation-factory/suite_validation_locking";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "suite-validation")!;

export default function SuiteValidationPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <SuiteValidationLocking
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          suiteId={v("suiteId")}
        />
      )}
    />
  );
}
