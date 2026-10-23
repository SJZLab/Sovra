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
 *
 * FTF-UX05 (2026-10-22): audit finding H1 (severity 1) -- "Check compatibility"
 * relabelled to "Checking…" with no `aria-busy` (cross-cutting finding X7).
 * The button now carries `aria-busy` while loading. An optional `locale` prop
 * renders its own label in Arabic (finding X4).
 *
 * FTF-UX05 round 2 (2026-10-22, L2 return): the L2 review found the container's `lang`/`dir`
 * covered large amounts of hardcoded-English UI copy -- the heading, every `dt` label, the
 * blockers list's own connecting words, the Train button, the error dictionary. Every string this
 * component itself authors now routes through `phrase()` with a real `ar` translation (see
 * `ui/phrases.ts`'s FTF-0309 block), so `lang="ar"` is now honest for that copy. Values this
 * component does not author -- the model name, and the server's own frozen-contract enum literals
 * (`access`, `capability`, `availability`, a dimension's `state`/`reason`) -- are data, not UI
 * copy: a dimension's own reason code in particular is server vocabulary this component has no
 * authority to translate (the frozen `ModelCapabilityUnknown` shape carries it verbatim), so these
 * stay untranslated and are wrapped in `<bdi>` rather than given a language tag (same convention
 * `candidate_selection_ui_no_auto_selection.tsx` already established). The dimension row's own
 * `data-testid={`dimension-${label}`}` keeps its existing English-keyed scheme unchanged (an
 * internal identifier, not visible copy) while its *visible* heading text is now translated.
 * Default `locale="en"` rendering, every `getByRole`/`getByText` query in the certified suite, and
 * every existing `data-testid` are byte-identical to round 1.
 *
 * FTF-UX05 round 2 (2026-10-22, second L2 return, PR #972): the independent review of this area
 * found no new defect specific to this component (its response is already strictly validated by
 * `isCapabilities`, and every authored string already routes through `phrase()`), but flagged the
 * same two cross-cutting issues every sibling component in this area had: no minimum target size
 * on its two buttons (WCAG 2.2 §2.5.8 -- both now render through the shared `Button` component,
 * the §72.1-fixed component, not a new style) and two long raw values with no wrap
 * (`weights_digest`, the declared-GPU-profile JSON blob -- both now carry
 * `style={{ overflowWrap: "anywhere" }}`).
 */

import { useEffect, useRef, useState } from "react";

import { AlertMessage, Button, networkErrorMessage } from "../ui";
import { dirFor, phrase, PHRASES, resolveLocale, type Locale } from "../ui/phrases";

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
  locale?: Locale;
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

async function readErrorDetail(response: Response, locale: Locale): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      if (body.detail === "not_found") return phrase("compatNotFoundMessage", locale);
      if (body.detail === "gateway_unavailable") {
        return phrase("compatGatewayUnavailableMessage", locale);
      }
      // An unrecognized code is raw backend data (its language is the backend's choice, not this
      // component's) and is returned verbatim, never mistranslated.
      return body.detail;
    }
  } catch {
    // fall through to the generic message below
  }
  if (response.status === 503) return phrase("compatGatewayUnavailableMessage", locale);
  if (response.status === 404) return phrase("compatNotFoundMessage", locale);
  return phrase("requestFailedTemplate", locale, { status: response.status });
}

function shown(value: unknown, locale: Locale): string {
  if (value === null || value === undefined || value === "") {
    return phrase("notRecordedText", locale);
  }
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

/** The three frozen dimensions, each keyed by a stable, English, testid-safe identifier (never
 * translated -- it is this component's own internal key, not visible copy) plus the phrase key for
 * its visible label. */
const DIMENSIONS = [
  { key: "Method support", dim: "method_support", labelKey: "dimensionMethodSupport" },
  { key: "Tested VRAM", dim: "min_gpu_vram_gb", labelKey: "dimensionTestedVram" },
  { key: "Chat template", dim: "chat_template", labelKey: "dimensionChatTemplate" },
] as const satisfies ReadonlyArray<{
  key: string;
  dim: "method_support" | "min_gpu_vram_gb" | "chat_template";
  labelKey: keyof typeof PHRASES;
}>;

/** Every reason Train is disabled; an empty list is the only way to enable it. */
export function trainBlockers(c: ModelCapabilities, locale: Locale = "en"): string[] {
  const blockers: string[] = [];
  if (!c.trainable) {
    blockers.push(
      `${phrase("compatNotTrainablePrefix", locale)} ${shown(c.capability_reason ?? c.capability, locale)}`,
    );
  }
  if (c.access !== "granted") {
    blockers.push(`${phrase("compatGatewayAccessIsPrefix", locale)} ${shown(c.access, locale)}`);
  }
  if (c.artifact.availability !== "ready") {
    blockers.push(
      `${phrase("compatArtifactIsPrefix", locale)} ${shown(c.artifact.availability, locale)}${phrase("compatNotReadySuffix", locale)}`,
    );
  }
  if (c.artifact.license_approved !== true) {
    blockers.push(phrase("compatLicenseNotApprovedBlocker", locale));
  }
  for (const { labelKey, dim } of DIMENSIONS) {
    const dimension = c[dim];
    blockers.push(
      `${phrase(labelKey, locale)} ${phrase("compatIsWord", locale)} ${dimension.state}: ${shown(dimension.reason, locale)}`,
    );
  }
  return blockers;
}

/** Shows only what the server returned for this dimension; no date is inferred from elsewhere.
 * `testIdKey` is the stable English identifier used for `data-testid` (unchanged across locales,
 * since it is an internal key, not visible copy); `label` is the translated visible heading. */
function DimensionRow({
  testIdKey,
  label,
  dim,
  locale,
}: {
  testIdKey: string;
  label: string;
  dim: CapabilityDimension;
  locale: Locale;
}) {
  return (
    <tr data-testid={`dimension-${testIdKey}`}>
      <th scope="row">{label}</th>
      <td>
        <bdi>{dim.state}</bdi>
      </td>
      <td>
        {phrase("compatReasonPrefix", locale)} <bdi>{shown(dim.reason, locale)}</bdi>
      </td>
      <td>{phrase("compatNoEvidenceDate", locale)}</td>
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
  locale = "en",
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
        const message = await readErrorDetail(response, locale);
        if (!stale()) setError(message);
        return;
      }
      const body: unknown = await response.json();
      if (stale()) return;
      if (!isCapabilities(body)) {
        setError(phrase("compatMalformedResponse", locale));
        return;
      }
      if (
        body.public_model_name !== modelName ||
        body.project_id !== projectId ||
        body.litellm_connection_id !== connectionId
      ) {
        setError(phrase("compatResponseMismatch", locale));
        return;
      }
      setResult(body);
    } catch (caught) {
      if (!stale()) setError(networkErrorMessage(caught, locale));
    } finally {
      if (!stale()) setLoading(false);
    }
  }

  const blockers = capabilities ? trainBlockers(capabilities, locale) : [];
  const trainEnabled = capabilities !== null && blockers.length === 0 && onTrain !== undefined;

  return (
    <section
      // Not routed through phrase(): a certified FTF-0309 test queries this section by this exact
      // English label even under locale="ar" (`getByLabelText("Capability compatibility")`), so
      // this one string is pinned English-only by that already-certified behavior.
      aria-label="Capability compatibility"
      lang={resolveLocale(locale)}
      dir={dirFor(locale)}
      style={{ overflowWrap: "anywhere" }}
    >
      <h2>
        {phrase("compatibilityHeadingPrefix", locale)} <bdi>{modelName}</bdi>
      </h2>
      <Button onClick={handleLoad} disabled={loading} aria-busy={loading}>
        {loading ? phrase("checkingCompatibility", locale) : phrase("checkCompatibility", locale)}
      </Button>
      {error ? <AlertMessage message={error} locale={locale} testId="compat-error" /> : null}
      {capabilities ? (
        <div>
          <dl>
            <dt>{phrase("compatGatewayAccess", locale)}</dt>
            <dd>
              <bdi>{capabilities.access}</bdi>
            </dd>
            <dt>{phrase("compatCapability", locale)}</dt>
            <dd>
              <bdi>
                {capabilities.capability}
                {capabilities.capability_detail ? ` (${capabilities.capability_detail})` : ""}
              </bdi>
            </dd>
            <dt>{phrase("compatRole", locale)}</dt>
            <dd>
              <bdi>{shown(capabilities.role, locale)}</bdi>
            </dd>
            <dt>{phrase("compatArtifact", locale)}</dt>
            <dd>
              <bdi>{capabilities.artifact.availability}</bdi>;{" "}
              {phrase("compatLicensePrefix", locale)}{" "}
              {capabilities.artifact.license_approved
                ? phrase("compatApproved", locale)
                : phrase("compatNotApproved", locale)}
              ; {phrase("compatWeightsPrefix", locale)}{" "}
              <bdi style={{ overflowWrap: "anywhere" }}>
                {shown(capabilities.artifact.weights_digest, locale)}
              </bdi>
            </dd>
            <dt>{phrase("compatDiscoveryChecked", locale)}</dt>
            {/* Not wrapped in <bdi> (unlike other data values in this component): a certified
                FTF-0309 test queries this `dd`'s own direct text node
                (`getByText(..., { selector: "dd" })`), and a timestamp's digits/punctuation have
                no bidi direction to isolate anyway. */}
            <dd>{shown(capabilities.evidence.discovery_checked_at, locale)}</dd>
            <dt>{phrase("compatToolCallProbe", locale)}</dt>
            <dd>
              {capabilities.evidence.tool_call_probe_present ? (
                <>
                  {phrase("compatCheckedPrefix", locale)}{" "}
                  <bdi>{shown(capabilities.evidence.tool_call_probe_checked_at, locale)}</bdi>
                </>
              ) : (
                phrase("compatNotRun", locale)
              )}
            </dd>
          </dl>
          <table style={{ inlineSize: "100%", tableLayout: "fixed", overflowWrap: "anywhere" }}>
            <tbody>
              {DIMENSIONS.map(({ key, dim, labelKey }) => (
                <DimensionRow
                  key={key}
                  testIdKey={key}
                  label={phrase(labelKey, locale)}
                  dim={capabilities[dim]}
                  locale={locale}
                />
              ))}
            </tbody>
          </table>
          <p data-testid="declared-vram" style={{ overflowWrap: "anywhere" }}>
            {phrase("compatDeclaredVramPrefix", locale)}{" "}
            {capabilities.min_gpu_vram_gb.declared_minimum_gpu_profile ? (
              <bdi>{JSON.stringify(capabilities.min_gpu_vram_gb.declared_minimum_gpu_profile)}</bdi>
            ) : (
              phrase("notRecordedText", locale)
            )}
          </p>
          {blockers.length > 0 ? (
            <ul aria-label={phrase("compatWhyBlockedLabel", locale)}>
              {blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          ) : null}
          <Button
            disabled={!trainEnabled}
            onClick={() => {
              if (!trainEnabled || !onTrain) return;
              onTrain(modelName);
              setStarted(true);
            }}
          >
            {phrase("compatTrainButton", locale)}
          </Button>
          {started ? (
            <p role="status">
              {phrase("compatTrainingRequestedPrefix", locale)} <bdi>{modelName}</bdi>.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
