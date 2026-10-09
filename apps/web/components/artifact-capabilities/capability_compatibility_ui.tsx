/**
 * Capability compatibility UI (FTF-0309, Section 2.2/6.7/39/68/77/81).
 *
 * Consumes only the frozen FTF-0309-P1 route in
 * `packages/contracts/openapi/03-interface.yaml`:
 *
 *   GET .../connections/litellm/{id}/models/{name}/capabilities?project_id=
 *
 * Acceptance ("Compatibility screen explains actual model/hardware/method/
 * template/license limits before choosing training"): every dimension the
 * server returns is rendered with its own dated evidence, and every explicit
 * `unknown` is shown with the server's reason code. Nothing is inferred
 * client-side and the model name is sent verbatim (no alias normalisation).
 *
 * Negative acceptance ("Unknown support or insufficient tested VRAM prevents
 * Train rather than fake auto recommendation"): the Train button is disabled
 * unless EVERY gate holds -- `trainable`, `access === "granted"`, a ready
 * artifact with an approved license, and method_support / min_gpu_vram_gb /
 * chat_template all known. The frozen contract allows ONLY `state: "unknown"`
 * for those three dimensions (`ModelCapabilityUnknown`), so a `known` state, any
 * other state, or a `tested_gb` / `memory_gb` field on a dimension is a
 * malformed response with no Train (control-plane ruling, round 3). A future
 * known state needs a separate, owner-routed contract change. Each blocker is
 * listed with its reason. A 404
 * or 503 is an error screen, never an empty recommendation (Law 17). The UI
 * never recommends a model: Train only reports an explicit user click.
 *
 * Authorization is the route's role gate (403 is surfaced as-is); a UI
 * control never authorizes the effect.
 *
 * Scope disclosed, not hidden (same precedent as `model_to_artifact_mapping_ui.tsx`):
 * no Next.js `app/` page tree yet (FTF-H029), so this component is DOM-tested
 * with Vitest + React Testing Library + jsdom and a mocked `fetch`; it is
 * UNWIRED to a page and to a training start until those exist. Today the
 * frozen route can only return `unknown` for the three tested dimensions, so
 * Train is disabled for every real response.
 */

import { useEffect, useRef, useState } from "react";

/** The frozen `ModelCapabilityUnknown`: `unknown` is the only state the contract allows today. */
export interface CapabilityDimension {
  state: "unknown";
  reason: string;
  declared_minimum_gpu_profile?: unknown;
  declared_evidence?: "declared";
}

export interface ModelCapabilities {
  litellm_connection_id: string;
  project_id: string;
  public_model_name: string;
  connection_status: string;
  access: "granted" | "not_granted";
  capability: string;
  trainable: boolean;
  capability_detail?: string;
  capability_reason?: string | null;
  role?: string | null;
  artifact: {
    artifact_id: string | null;
    availability: "none" | "registered" | "ready" | "archived";
    license_approved: boolean;
    weights_digest: string | null;
  };
  catalog_version: number;
  method_support: CapabilityDimension;
  min_gpu_vram_gb: CapabilityDimension;
  chat_template: CapabilityDimension;
  evidence: {
    discovery_checked_at: string | null;
    tool_call_probe_checked_at: string | null;
    tool_call_probe_present: boolean;
  };
}

export interface CapabilityCompatibilityUIProps {
  apiBaseUrl: string;
  organizationId: string;
  connectionId: string;
  projectId: string;
  modelName: string;
  /** Called only by an explicit click on an enabled Train button. */
  onTrain?: (modelName: string) => void;
}

function trimBase(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/$/, "");
}

/** Encode each path segment but keep '/', the route's model_name is a path. */
function encodeModelName(modelName: string): string {
  return modelName.split("/").map(encodeURIComponent).join("/");
}

export function capabilitiesUrl(
  apiBaseUrl: string,
  organizationId: string,
  connectionId: string,
  modelName: string,
  projectId: string,
): string {
  return (
    `${trimBase(apiBaseUrl)}/api/v1/organizations/${organizationId}` +
    `/connections/litellm/${connectionId}/models/${encodeModelName(modelName)}/capabilities` +
    `?project_id=${encodeURIComponent(projectId)}`
  );
}

const NOT_FOUND_MESSAGE = "The connection, artifact or project was not found.";
const GATEWAY_UNAVAILABLE_MESSAGE =
  "The gateway could not be reached, so compatibility could not be determined. No recommendation is made.";
const ERROR_MESSAGES: Record<string, string> = {
  not_found: NOT_FOUND_MESSAGE,
  gateway_unavailable: GATEWAY_UNAVAILABLE_MESSAGE,
};

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return ERROR_MESSAGES[body.detail] ?? body.detail;
    }
  } catch {
    // fall through to the generic message below
  }
  if (response.status === 503) return GATEWAY_UNAVAILABLE_MESSAGE;
  if (response.status === 404) return NOT_FOUND_MESSAGE;
  return `request failed (${response.status})`;
}

function networkErrorMessage(caught: unknown): string {
  const reason = caught instanceof Error && caught.message ? caught.message : "unknown error";
  return `network error: ${reason}`;
}

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "not recorded";
  return String(value);
}

const ACCESS_VALUES = ["granted", "not_granted"];
const CAPABILITY_VALUES = [
  "trainable_open_weight",
  "managed_fine_tunable",
  "inference_only",
  "unknown",
];
const AVAILABILITY_VALUES = ["none", "registered", "ready", "archived"];
const TOP_LEVEL_KEYS = new Set([
  "litellm_connection_id",
  "project_id",
  "public_model_name",
  "connection_status",
  "access",
  "capability",
  "trainable",
  "capability_detail",
  "capability_reason",
  "role",
  "artifact",
  "catalog_version",
  "method_support",
  "min_gpu_vram_gb",
  "chat_template",
  "evidence",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isStringOrNull(value: unknown): boolean {
  return value === null || typeof value === "string";
}

const DIMENSION_KEYS = new Set([
  "state",
  "reason",
  "declared_minimum_gpu_profile",
  "declared_evidence",
]);

/** A dimension is well formed only as the frozen `unknown` shape carrying the server's reason. */
function isDimension(value: unknown): value is CapabilityDimension {
  if (!isRecord(value)) return false;
  for (const key of Object.keys(value)) if (!DIMENSION_KEYS.has(key)) return false;
  if (value.state !== "unknown") return false;
  if (typeof value.reason !== "string" || value.reason === "") return false;
  if ("declared_evidence" in value && value.declared_evidence !== "declared") return false;
  return true;
}

/**
 * Strict parse of the frozen `ModelCapabilities` schema (additionalProperties
 * false, every required field typed). Anything else is a typed error, never a
 * recommendation.
 */
function isCapabilities(body: unknown): body is ModelCapabilities {
  if (!isRecord(body)) return false;
  for (const key of Object.keys(body)) if (!TOP_LEVEL_KEYS.has(key)) return false;
  const artifact = body.artifact;
  const evidence = body.evidence;
  return (
    typeof body.litellm_connection_id === "string" &&
    typeof body.project_id === "string" &&
    typeof body.public_model_name === "string" &&
    typeof body.connection_status === "string" &&
    typeof body.access === "string" &&
    ACCESS_VALUES.includes(body.access) &&
    typeof body.capability === "string" &&
    CAPABILITY_VALUES.includes(body.capability) &&
    typeof body.trainable === "boolean" &&
    (body.capability_detail === undefined || typeof body.capability_detail === "string") &&
    (body.capability_reason === undefined || isStringOrNull(body.capability_reason)) &&
    (body.role === undefined || isStringOrNull(body.role)) &&
    isRecord(artifact) &&
    isStringOrNull(artifact.artifact_id) &&
    typeof artifact.availability === "string" &&
    AVAILABILITY_VALUES.includes(artifact.availability) &&
    typeof artifact.license_approved === "boolean" &&
    isStringOrNull(artifact.weights_digest) &&
    typeof body.catalog_version === "number" &&
    Number.isInteger(body.catalog_version) &&
    body.catalog_version >= 0 &&
    isRecord(evidence) &&
    isStringOrNull(evidence.discovery_checked_at) &&
    isStringOrNull(evidence.tool_call_probe_checked_at) &&
    typeof evidence.tool_call_probe_present === "boolean" &&
    isDimension(body.method_support) &&
    isDimension(body.min_gpu_vram_gb) &&
    isDimension(body.chat_template)
  );
}

/** Every reason Train is disabled; an empty list is the only way to enable it. */
export function trainBlockers(c: ModelCapabilities): string[] {
  const blockers: string[] = [];
  if (!c.trainable) blockers.push(`Not trainable: ${shown(c.capability_reason ?? c.capability)}`);
  if (c.access !== "granted") blockers.push(`Gateway access is ${shown(c.access)}`);
  if (c.artifact.availability !== "ready") {
    blockers.push(`Trainable artifact is ${shown(c.artifact.availability)}, not ready`);
  }
  if (c.artifact.license_approved !== true) blockers.push("Artifact license is not approved");
  const dimensions: Array<[string, CapabilityDimension]> = [
    ["Method support", c.method_support],
    ["Tested VRAM", c.min_gpu_vram_gb],
    ["Chat template", c.chat_template],
  ];
  for (const [label, dim] of dimensions) {
    blockers.push(`${label} is ${dim.state}: ${shown(dim.reason)}`);
  }
  return blockers;
}

/** Shows only what the server returned for this dimension; no date is inferred from elsewhere. */
function DimensionRow({ label, dim }: { label: string; dim: CapabilityDimension }) {
  return (
    <tr data-testid={`dimension-${label}`}>
      <th scope="row">{label}</th>
      <td>{dim.state}</td>
      <td>{`reason: ${shown(dim.reason)}`}</td>
      <td>no evidence date returned by the server</td>
    </tr>
  );
}

export function CapabilityCompatibilityUI({
  apiBaseUrl,
  organizationId,
  connectionId,
  projectId,
  modelName,
  onTrain,
}: CapabilityCompatibilityUIProps) {
  const identity = JSON.stringify([apiBaseUrl, organizationId, connectionId, projectId, modelName]);
  const [result, setResult] = useState<ModelCapabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [started, setStarted] = useState(false);
  const latestRequest = useRef(0);

  // Any org/connection/project/model change invalidates the result before the next commit (no frame
  // with a stale Train) and any in-flight response (its request number is no longer the latest).
  const [seenIdentity, setSeenIdentity] = useState(identity);
  if (seenIdentity !== identity) {
    setSeenIdentity(identity);
    setResult(null);
    setError(null);
    setLoading(false);
    setStarted(false);
  }
  useEffect(() => {
    latestRequest.current += 1;
  }, [identity]);

  const capabilities = result;

  async function handleLoad() {
    const request = ++latestRequest.current;
    const stale = () => request !== latestRequest.current;
    setLoading(true);
    setError(null);
    setResult(null);
    setStarted(false);
    try {
      const response = await fetch(
        capabilitiesUrl(apiBaseUrl, organizationId, connectionId, modelName, projectId),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        const message = await readErrorDetail(response);
        if (!stale()) setError(message);
        return;
      }
      const body: unknown = await response.json();
      if (stale()) return;
      if (!isCapabilities(body)) {
        setError("malformed capabilities response");
        return;
      }
      if (
        body.public_model_name !== modelName ||
        body.project_id !== projectId ||
        body.litellm_connection_id !== connectionId
      ) {
        setError("capabilities response does not match the request");
        return;
      }
      setResult(body);
    } catch (caught) {
      if (!stale()) setError(networkErrorMessage(caught));
    } finally {
      if (!stale()) setLoading(false);
    }
  }

  const blockers = capabilities ? trainBlockers(capabilities) : [];
  const trainEnabled = capabilities !== null && blockers.length === 0 && onTrain !== undefined;

  return (
    <section aria-label="Capability compatibility">
      <h2>Compatibility: {modelName}</h2>
      <button type="button" onClick={handleLoad} disabled={loading}>
        {loading ? "Checking..." : "Check compatibility"}
      </button>
      {error ? <p role="alert">{error}</p> : null}
      {capabilities ? (
        <div>
          <dl>
            <dt>Gateway access</dt>
            <dd>{capabilities.access}</dd>
            <dt>Capability</dt>
            <dd>{`${capabilities.capability}${capabilities.capability_detail ? ` (${capabilities.capability_detail})` : ""}`}</dd>
            <dt>Role</dt>
            <dd>{shown(capabilities.role)}</dd>
            <dt>Artifact</dt>
            <dd>{`${capabilities.artifact.availability}; license ${capabilities.artifact.license_approved ? "approved" : "not approved"}; weights ${shown(capabilities.artifact.weights_digest)}`}</dd>
            <dt>Discovery checked</dt>
            <dd>{shown(capabilities.evidence.discovery_checked_at)}</dd>
            <dt>Tool-call probe</dt>
            <dd>
              {capabilities.evidence.tool_call_probe_present
                ? `checked ${shown(capabilities.evidence.tool_call_probe_checked_at)}`
                : "not run"}
            </dd>
          </dl>
          <table>
            <tbody>
              <DimensionRow label="Method support" dim={capabilities.method_support} />
              <DimensionRow label="Tested VRAM" dim={capabilities.min_gpu_vram_gb} />
              <DimensionRow label="Chat template" dim={capabilities.chat_template} />
            </tbody>
          </table>
          <p data-testid="declared-vram">
            {`Declared minimum GPU profile (declared, not tested): ${
              capabilities.min_gpu_vram_gb.declared_minimum_gpu_profile
                ? JSON.stringify(capabilities.min_gpu_vram_gb.declared_minimum_gpu_profile)
                : "not recorded"
            }`}
          </p>
          {blockers.length > 0 ? (
            <ul aria-label="Why training is blocked">
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            disabled={!trainEnabled}
            onClick={() => {
              if (!trainEnabled || !onTrain) return;
              onTrain(modelName);
              setStarted(true);
            }}
          >
            Train
          </button>
          {started ? <p role="status">Training requested by you for {modelName}.</p> : null}
        </div>
      ) : null}
    </section>
  );
}
