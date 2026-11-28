import { useState } from "react";
import type { ReactElement } from "react";

import {
  GitHubAccessibleRepositorySelector,
  type GitHubRepositorySummary,
} from "../components/target-application/github_accessible_repository_selector_ui";
import { GitHubBranchRefSelector } from "../components/target-application/github_branch_ref_selector_immutable_sha_resolver";
import { JourneyPage } from "../lib/JourneyPage";
import { JOURNEYS } from "../lib/journeys";

const journey = JOURNEYS.find((j) => j.slug === "github-branch")!;

interface Chosen {
  repository: GitHubRepositorySummary;
  credential: string;
}

// Two steps in one page: pick a repository, then pick a branch or ref. The GitHub credential the
// user typed in step 1 is carried to step 2 in component state only: it is never put in a URL, in
// storage, or in a query parameter.
function GitHubBranchFlow({
  apiBaseUrl,
  organizationId,
  projectId,
  targetApplicationConnectionId,
}: {
  apiBaseUrl: string;
  organizationId: string;
  projectId: string;
  targetApplicationConnectionId: string;
}): ReactElement {
  const [chosen, setChosen] = useState<Chosen | null>(null);
  return (
    <>
      <GitHubAccessibleRepositorySelector
        apiBaseUrl={apiBaseUrl}
        organizationId={organizationId}
        projectId={projectId}
        onSelectWithCredential={(repository, credential) => setChosen({ repository, credential })}
      />
      {chosen && (
        <GitHubBranchRefSelector
          key={chosen.repository.repository_identity}
          apiBaseUrl={apiBaseUrl}
          organizationId={organizationId}
          projectId={projectId}
          credential={chosen.credential}
          repositoryIdentity={chosen.repository.repository_identity}
          targetApplicationConnectionId={targetApplicationConnectionId}
          defaultBranch={chosen.repository.default_branch}
        />
      )}
    </>
  );
}

export default function GitHubBranchPage(): ReactElement {
  return (
    <JourneyPage
      journey={journey}
      render={(v, apiBaseUrl) => (
        <GitHubBranchFlow
          apiBaseUrl={apiBaseUrl}
          organizationId={v("organizationId")}
          projectId={v("projectId")}
          targetApplicationConnectionId={v("targetApplicationConnectionId")}
        />
      )}
    />
  );
}
