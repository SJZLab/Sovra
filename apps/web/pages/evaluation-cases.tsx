import type { ReactElement } from "react";

import { EvaluationCaseEditorImport } from "../components/evaluation-factory/evaluation_case_editor_import";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "evaluation-cases")!;

export default function EvaluationCasesPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <EvaluationCaseEditorImport
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          suiteId={v("suiteId")}
        />
      )}
    />
  );
}
