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
];
