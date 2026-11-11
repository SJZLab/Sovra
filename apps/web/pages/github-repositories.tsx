import type { ReactElement } from "react";

import { GitHubAccessibleRepositorySelector } from "../components/target-application/github_accessible_repository_selector_ui";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "github-repositories")!;

export default function GithubRepositoriesPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <GitHubAccessibleRepositorySelector
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
        />
      )}
    />
  );
}
