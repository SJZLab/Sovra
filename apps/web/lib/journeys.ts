// One registry for every journey the front door links to. The front door renders a form per entry,
// and each journey page reads the same parameter names. A journey is reached only from "/" (Law 6).
export interface JourneyParam {
  name: string;
  label: string;
}

export interface Journey {
  slug: string;
  title: string;
  params: JourneyParam[];
}

export const JOURNEYS: Journey[] = [
  {
    slug: "artifact-mapping",
    title: "Map a model to a trainable artifact",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "connectionId", label: "Connection ID" },
      { name: "modelName", label: "Model name" },
    ],
  },
  {
    slug: "model-catalog",
    title: "Model catalog",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "connectionId", label: "Connection ID" },
    ],
  },
  {
    slug: "capability-compatibility",
    title: "Model capability compatibility",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "connectionId", label: "Connection ID" },
      { name: "projectId", label: "Project ID" },
      { name: "modelName", label: "Model name" },
    ],
  },
  {
    slug: "dataset-upload",
    title: "Resumable dataset upload",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "datasetSourceConnectionId", label: "Dataset source connection ID" },
    ],
  },
  {
    slug: "requirements",
    title: "Project requirements",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "securityProfile", label: "Security profile" },
    ],
  },
  {
    slug: "project-setup",
    title: "Set up a project",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "workspaceId", label: "Workspace ID" },
    ],
  },
  {
    slug: "cost-profile",
    title: "Cost profile and cost-per-success search",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
    ],
  },
  {
    slug: "github-repositories",
    title: "GitHub repositories",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
    ],
  },
  {
    slug: "candidate-selection",
    title: "Candidate selection",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "connectionId", label: "Connection ID" },
      { name: "snapshotId", label: "Snapshot ID" },
    ],
  },
  {
    slug: "manifest-review",
    title: "Manifest review",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "manifestId", label: "Manifest ID" },
    ],
  },
  {
    slug: "mandate-editor",
    title: "Mandate editor",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "manifestId", label: "Manifest ID" },
    ],
  },
  {
    slug: "dataset-issues",
    title: "Dataset issue resolution",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "datasetId", label: "Dataset ID" },
    ],
  },
  {
    slug: "dataset-source",
    title: "Dataset source and purpose authorization",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
    ],
  },
  {
    slug: "evaluation-cases",
    title: "Evaluation case editor and import",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "suiteId", label: "Suite ID" },
    ],
  },
  {
    slug: "github-branch",
    title: "GitHub repository and branch",
    params: [
      { name: "organizationId", label: "Organization ID" },
      { name: "projectId", label: "Project ID" },
      { name: "targetApplicationConnectionId", label: "Target application connection ID" },
    ],
  },
];
