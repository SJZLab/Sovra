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
 * chat_template all `known` (fail closed: any other state, including a state
 * this file does not recognise, blocks). Tested VRAM below the artifact's
 * declared minimum blocks too. Each blocker is listed with its reason. A 404
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

import { useState } from "react";

export interface CapabilityDimension {
  state: string;
  reason?: string;
  declared_minimum_gpu_profile?: Record<string, unknown> | null;
  declared_evidence?: string;
  tested_gb?: number;
}

export interface ModelCapabilities {
  litellm_connection_id: string;
  project_id: string;
  public_model_name: string;
  connection_status: string;
  access: string;
  capability: string;
  trainable: boolean;
  capability_detail?: string;
  capability_reason?: string | null;
  role?: string | null;
  artifact: {
    artifact_id: string | null;
    availability: string;
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

function isDimension(value: unknown): value is CapabilityDimension {
  return (
    !!value && typeof value === "object" && typeof (value as CapabilityDimension).state === "string"
  );
}

function isCapabilities(body: unknown): body is ModelCapabilities {
  if (!body || typeof body !== "object") return false;
  const c = body as Partial<ModelCapabilities>;
  return (
    typeof c.trainable === "boolean" &&
    typeof c.access === "string" &&
    !!c.artifact &&
    typeof c.artifact === "object" &&
    !!c.evidence &&
    typeof c.evidence === "object" &&
    isDimension(c.method_support) &&
    isDimension(c.min_gpu_vram_gb) &&
    isDimension(c.chat_template)
  );
}

function declaredVramGb(profile: unknown): number | null {
  if (!profile || typeof profile !== "object") return null;
  const gb = (profile as Record<string, unknown>).memory_gb;
  return typeof gb === "number" ? gb : null;
}

/** Every reason Train is disabled; an empty list is the only way to enable it. */
export function trainBlockers(c: ModelCapabilities): string[] {
  const blockers: string[] = [];
  if (!c.trainable) blockers.push(`Not trainable: ${shown(c.capability_reason ?? c.capability)}`);
  if (c.access !== "granted") blockers.push(`Gateway access is ${shown(c.access)}`);
  if (c.artifact.availability !== "ready") {
    blockers.push(`Trainable artifact is ${shown(c.artifact.availability)}, not ready`);
  }
  if (!c.artifact.license_approved) blockers.push("Artifact license is not approved");
  const dimensions: Array<[string, CapabilityDimension]> = [
    ["Method support", c.method_support],
    ["Tested VRAM", c.min_gpu_vram_gb],
    ["Chat template", c.chat_template],
  ];
  for (const [label, dim] of dimensions) {
    if (dim.state !== "known") {
      blockers.push(`${label} is ${dim.state}: ${shown(dim.reason)}`);
    }
  }
  if (c.min_gpu_vram_gb.state === "known") {
    const need = declaredVramGb(c.min_gpu_vram_gb.declared_minimum_gpu_profile);
    const tested = c.min_gpu_vram_gb.tested_gb;
    if (typeof tested !== "number" || (need !== null && tested < need)) {
      blockers.push(
        `Tested VRAM ${shown(tested)} GB is below the declared minimum ${shown(need)} GB`,
      );
    }
  }
  return blockers;
}

function DimensionRow({
  label,
  dim,
  checkedAt,
}: {
  label: string;
  dim: CapabilityDimension;
  checkedAt: string | null;
}) {
  return (
    <tr data-testid={`dimension-${label}`}>
      <th scope="row">{label}</th>
      <td>{dim.state}</td>
      <td>{dim.state === "known" ? "" : `reason: ${shown(dim.reason)}`}</td>
      <td>{checkedAt ? `checked ${checkedAt}` : "evidence date not recorded"}</td>
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
  const [capabilities, setCapabilities] = useState<ModelCapabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [started, setStarted] = useState(false);

  async function handleLoad() {
    setLoading(true);
    setError(null);
    setCapabilities(null);
    setStarted(false);
    try {
      const response = await fetch(
        capabilitiesUrl(apiBaseUrl, organizationId, connectionId, modelName, projectId),
        { method: "GET", credentials: "include" },
      );
      if (!response.ok) {
        setError(await readErrorDetail(response));
        return;
      }
      const body: unknown = await response.json();
      if (!isCapabilities(body)) {
        setError("malformed capabilities response");
        return;
      }
      setCapabilities(body);
    } catch (caught) {
      setError(networkErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }

  const blockers = capabilities ? trainBlockers(capabilities) : [];
  const trainEnabled = capabilities !== null && blockers.length === 0;

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
              <DimensionRow
                label="Method support"
                dim={capabilities.method_support}
                checkedAt={capabilities.evidence.discovery_checked_at}
              />
              <DimensionRow
                label="Tested VRAM"
                dim={capabilities.min_gpu_vram_gb}
                checkedAt={capabilities.evidence.discovery_checked_at}
              />
              <DimensionRow
                label="Chat template"
                dim={capabilities.chat_template}
                checkedAt={capabilities.evidence.discovery_checked_at}
              />
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
              setStarted(true);
              onTrain?.(modelName);
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
