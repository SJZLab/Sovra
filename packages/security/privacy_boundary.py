"""FTF-H027 -- the declared private data boundary, enforced at each effect (§11-12, §65, §77, §81).

FTF-0901 `enforce_effect` decides whether a destination *kind* is allowed under
the profile in force, but it trusts its caller to say which kind a destination
is. FTF-H006 `guarded_fetch` validates a resolved address against an allowlist,
but lets any public address through. Neither one knows where the
organization's boundary is. This module is that missing piece: a closed,
validated manifest (`infra/policies/private_data_plane.yaml`) records the
boundary (account, cluster, network, storage, region, operator identities)
and, for every sensitive surface, the exact internal endpoints that surface
may reach. A destination's kind is derived from that manifest. It is never
taken from the caller.

* `parse_private_boundary` / `load_private_boundary` refuse anything outside
  the closed shape: an unknown or missing key or surface, a wrong type, a
  public, default-route, link-local, IPv4-mapped or cloud-metadata network, a
  host outside its own network, a port outside 1..65535, or the same endpoint
  name declared with two different meanings. A surface with no endpoint is
  allowed and means "nothing on this surface may leave the process".
* `classify_destination` maps (surface, URL) to a 0901 `Destination`. A URL is
  internal only when its exact (scheme, host, port) is declared on that same
  surface. A host spelled another way, an endpoint declared on a different
  surface, or any undeclared host is external. Classification never resolves
  DNS.
* `boundary_fetch` is the only effect path. It classifies, then runs
  `enforce_effect` with the boundary's own profile as a floor, so a weaker
  organization, project or job setting cannot lower it. An external
  destination is refused by 0901 (`zero_egress`/`air_gapped`, or `standard`
  with no authorization). If 0901 would allow it, this module still refuses
  with `destination_outside_boundary`: an authorized external route belongs
  to its own provider adapter (FTF-0617/0907), never to the boundary fetcher.
  An internal destination is resolved once. Every address must pass H006
  `validate_resolved_address` AND fall inside the endpoint's own declared
  network, which closes DNS rebinding to a public address (H006 alone admits
  public addresses). The connection is then pinned to that exact address
  through H006's own pinned transport. Redirects are never followed.
* The `telemetry` surface accepts no raw body. `send_boundary_telemetry`
  builds the event with FTF-0903 `emit_event` first, so content is refused
  before any socket opens. A worker cannot post an arbitrary payload to the
  permitted collector through this module (§12).

Errors carry a stable `code`, and their messages are constant: they never
echo a host, URL, path or body (§65.4).

Scope (disclosed): this is process-scope enforcement. A malicious worker that
opens its own raw socket bypasses any in-process control. Cluster-scope CNI
enforcement of the same boundary (DNS resolver, IPv6, metadata, pod RBAC) is
FTF-H027-P3, and real Temporal history inspection is FTF-H027-P2. A passing
run means the tested controls passed in that environment. It never means
"no leakage is possible": operator access, model memorization and a
privileged-host compromise stay outside it, and the manifest's
`threat_model_exclusions` must name them.

UNWIRED: no product service calls `boundary_fetch` yet. The surfaces it
polices are separate, dependency-gated tasks (the FTF-0901/0903/0906
blank-`proving_symbol` precedent).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import urlsplit

import yaml

from app.services.project_security_profile import (
    SECURITY_PROFILE_LEVELS,
    resolve_effective_security_profile,
)
from packages.security.content_free_telemetry import ContentRef, TelemetryEvent, emit_event
from packages.security.outbound_guard import (
    OutboundGuardError,
    _build_connection,
    resolve_and_validate,
)
from packages.security.security_profile_enforcement import (
    Destination,
    EffectDecision,
    EffectRequest,
    RouteAuthorization,
    enforce_effect,
)
from packages.security.url_policy import (
    _CLOUD_METADATA_HOSTNAMES,
    _CLOUD_METADATA_NETWORKS,
    ConnectionPolicy,
    OutboundPolicyError,
    normalize_host_literal,
    validate_resolved_address,
    validate_url_shape,
)

__all__ = [
    "BOUNDARY_SURFACES",
    "SURFACE_TO_EFFECT_SURFACE",
    "ENDPOINT_SCHEMES",
    "FETCH_SCHEMES",
    "THREAT_MODEL_EXCLUSIONS",
    "REQUIRED_THREAT_MODEL_EXCLUSIONS",
    "PrivacyBoundaryError",
    "InvalidBoundaryManifestError",
    "InvalidBoundaryRequestError",
    "DestinationOutsideBoundaryError",
    "ResolvedOutsideBoundaryError",
    "RawTelemetryRefusedError",
    "BoundaryTransportError",
    "BoundaryEndpoint",
    "BoundaryDeployment",
    "PrivateBoundary",
    "BoundaryClassification",
    "BoundaryFetchResult",
    "parse_private_boundary",
    "load_private_boundary",
    "boundary_digest",
    "classify_destination",
    "endpoint_connection_policy",
    "boundary_fetch",
    "send_boundary_telemetry",
]

# The goal's surfaces (source, previews, data, embeddings, training, judge,
# registry, history, browser) plus the telemetry collector.
BOUNDARY_SURFACES: tuple[str, ...] = (
    "source",
    "preview",
    "data",
    "embedding",
    "training",
    "judge",
    "registry",
    "history",
    "browser",
    "telemetry",
)
# Each boundary surface is policed under the FTF-0901 surface it belongs to.
SURFACE_TO_EFFECT_SURFACE: Mapping[str, str] = MappingProxyType(
    {
        "source": "source",
        "preview": "data",
        "data": "data",
        "embedding": "model",
        "training": "model",
        "judge": "judge",
        "registry": "storage",
        "history": "history",
        "browser": "browser",
        "telemetry": "history",
    }
)
ENDPOINT_SCHEMES: tuple[str, ...] = ("http", "https", "tcp")
FETCH_SCHEMES: tuple[str, ...] = ("http", "https")
THREAT_MODEL_EXCLUSIONS: tuple[str, ...] = (
    "authorized_operator_access",
    "model_memorization",
    "privileged_host_compromise",
    "ciphertext_only_capture",
)
# §11.1 / negative acceptance: the remaining operator and memorization risks are
# always reported, never silently dropped from a boundary declaration.
REQUIRED_THREAT_MODEL_EXCLUSIONS: frozenset[str] = frozenset(THREAT_MODEL_EXCLUSIONS)

# The only networks an endpoint may live in. Explicit and auditable: RFC 1918,
# IPv4 loopback, IPv6 loopback and IPv6 unique-local. Link-local (and with it
# 169.254.169.254), CGNAT (100.100.100.200), IPv4-mapped and NAT64 ranges are
# outside every one of them.
_PERMITTED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)

_SCHEMA_VERSION = 1
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.:/@-]{0,127}")
_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DNS_NAME_RE = re.compile(rf"{_DNS_LABEL}(?:\.{_DNS_LABEL}){{0,9}}")
_MAX_ENDPOINTS_PER_SURFACE = 32
_MAX_OPERATORS = 32
_MAX_BODY_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_HEADERS = 32
_MAX_TIMEOUT_SECONDS = 60.0

_TOP_KEYS = frozenset(
    {
        "schema_version",
        "boundary_id",
        "profile",
        "deployment",
        "threat_model_exclusions",
        "surfaces",
    }
)
_DEPLOYMENT_TEXT_KEYS = ("account", "cluster", "network", "storage", "region")
_DEPLOYMENT_KEYS = frozenset((*_DEPLOYMENT_TEXT_KEYS, "operator_identities"))
_ENDPOINT_KEYS = frozenset({"name", "scheme", "host", "port", "network"})


class PrivacyBoundaryError(Exception):
    """Base. Every subclass carries a stable `code`, and messages never echo input."""

    code = "privacy_boundary_error"


class InvalidBoundaryManifestError(PrivacyBoundaryError):
    code = "boundary_manifest_invalid"


class InvalidBoundaryRequestError(PrivacyBoundaryError):
    code = "boundary_request_invalid"


class DestinationOutsideBoundaryError(PrivacyBoundaryError):
    """The destination is not a declared endpoint of this surface. The boundary
    fetcher never performs it, even where a profile would allow it."""

    code = "destination_outside_boundary"


class ResolvedOutsideBoundaryError(PrivacyBoundaryError):
    """A declared endpoint resolved to an address outside its own declared
    network (DNS rebinding), or to an address H006 refuses."""

    code = "resolved_outside_boundary"


class RawTelemetryRefusedError(PrivacyBoundaryError):
    """The telemetry surface takes only FTF-0903 events, never a raw body."""

    code = "raw_telemetry_refused"


class BoundaryTransportError(PrivacyBoundaryError):
    """An authorized, in-boundary connection failed (refused, reset, TLS,
    timeout, oversized response). Stays a failure (Law 17)."""

    code = "boundary_transport_failed"


Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class BoundaryEndpoint:
    name: str
    scheme: str
    host: str
    port: int
    network: Network


@dataclass(frozen=True)
class BoundaryDeployment:
    account: str
    cluster: str
    network: str
    storage: str
    region: str
    operator_identities: tuple[str, ...]


@dataclass(frozen=True)
class PrivateBoundary:
    boundary_id: str
    profile: str
    deployment: BoundaryDeployment
    threat_model_exclusions: tuple[str, ...]
    surfaces: Mapping[str, tuple[BoundaryEndpoint, ...]]
    digest: str


@dataclass(frozen=True)
class BoundaryClassification:
    surface: str
    destination: Destination
    endpoint: BoundaryEndpoint | None


@dataclass(frozen=True)
class BoundaryFetchResult:
    surface: str
    endpoint_name: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    decision: EffectDecision


# -- manifest validation ---------------------------------------------------


def _manifest_error(message: str) -> InvalidBoundaryManifestError:
    return InvalidBoundaryManifestError(message)


def _exact_mapping(value: object, keys: frozenset[str], what: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise _manifest_error(f"{what} must be a mapping")
    if set(value) != keys:
        raise _manifest_error(f"{what} must have exactly its declared keys")
    return value


def _pattern(value: object, pattern: re.Pattern[str], what: str) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise _manifest_error(f"{what} is malformed")
    return value


def _list(value: object, what: str, *, maximum: int, allow_empty: bool) -> list[object]:
    if type(value) is not list:
        raise _manifest_error(f"{what} must be a list")
    if not allow_empty and not value:
        raise _manifest_error(f"{what} must not be empty")
    if len(value) > maximum:
        raise _manifest_error(f"{what} is too long")
    return value


def _permitted_network(value: object, what: str) -> Network:
    if type(value) is not str:
        raise _manifest_error(f"{what} must be a CIDR string")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise _manifest_error(f"{what} is not a canonical CIDR") from exc
    if not any(
        network.version == allowed.version and network.subnet_of(allowed)  # type: ignore[arg-type]
        for allowed in _PERMITTED_NETWORKS
    ):
        raise _manifest_error(f"{what} is outside every permitted private network")
    if any(
        network.version == metadata.version and network.overlaps(metadata)
        for metadata in _CLOUD_METADATA_NETWORKS
    ):
        raise _manifest_error(f"{what} contains a cloud-metadata address")
    return network


def _endpoint_host(value: object, network: Network, what: str) -> str:
    if type(value) is not str:
        raise _manifest_error(f"{what} must be a string")
    try:
        literal = ipaddress.ip_address(value)
    except ValueError:
        literal = None
    if literal is not None:
        if str(literal) != value:
            raise _manifest_error(f"{what} must be a canonical IP literal")
        if literal not in network:
            raise _manifest_error(f"{what} is outside its own declared network")
        return value
    if normalize_host_literal(value) is not None:
        raise _manifest_error(f"{what} is an alternate IP encoding")
    if not _DNS_NAME_RE.fullmatch(value):
        raise _manifest_error(f"{what} is not a lowercase DNS name")
    if value in _CLOUD_METADATA_HOSTNAMES:
        raise _manifest_error(f"{what} names a cloud-metadata service")
    return value


def _endpoint(value: object, what: str) -> BoundaryEndpoint:
    raw = _exact_mapping(value, _ENDPOINT_KEYS, what)
    name = _pattern(raw["name"], _ID_RE, f"{what} name")
    scheme = raw["scheme"]
    if type(scheme) is not str or scheme not in ENDPOINT_SCHEMES:
        raise _manifest_error(f"{what} scheme is not permitted")
    port = raw["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise _manifest_error(f"{what} port is out of range")
    network = _permitted_network(raw["network"], f"{what} network")
    host = _endpoint_host(raw["host"], network, f"{what} host")
    return BoundaryEndpoint(name, scheme, host, port, network)


def _canonical(
    boundary_id: str,
    profile: str,
    deployment: BoundaryDeployment,
    exclusions: tuple[str, ...],
    surfaces: Mapping[str, tuple[BoundaryEndpoint, ...]],
) -> bytes:
    document = {
        "schema_version": _SCHEMA_VERSION,
        "boundary_id": boundary_id,
        "profile": profile,
        "deployment": {
            **{key: getattr(deployment, key) for key in _DEPLOYMENT_TEXT_KEYS},
            "operator_identities": list(deployment.operator_identities),
        },
        "threat_model_exclusions": list(exclusions),
        "surfaces": {
            surface: [
                {
                    "name": e.name,
                    "scheme": e.scheme,
                    "host": e.host,
                    "port": e.port,
                    "network": str(e.network),
                }
                for e in surfaces[surface]
            ]
            for surface in BOUNDARY_SURFACES
        },
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_private_boundary(document: object) -> PrivateBoundary:
    """Validate one boundary document (the parsed YAML mapping). Fail closed."""
    raw = _exact_mapping(document, _TOP_KEYS, "boundary manifest")
    version = raw["schema_version"]
    if type(version) is not int or version != _SCHEMA_VERSION:
        raise _manifest_error("boundary manifest schema_version is not supported")
    boundary_id = _pattern(raw["boundary_id"], _ID_RE, "boundary_id")
    profile = raw["profile"]
    if type(profile) is not str or profile not in SECURITY_PROFILE_LEVELS:
        raise _manifest_error("boundary profile is not a known security profile")

    dep = _exact_mapping(raw["deployment"], _DEPLOYMENT_KEYS, "deployment")
    texts = {
        key: _pattern(dep[key], _TEXT_RE, f"deployment {key}") for key in _DEPLOYMENT_TEXT_KEYS
    }
    operators = _list(
        dep["operator_identities"], "operator_identities", maximum=_MAX_OPERATORS, allow_empty=False
    )
    operator_ids = tuple(_pattern(op, _ID_RE, "operator identity") for op in operators)
    if len(set(operator_ids)) != len(operator_ids):
        raise _manifest_error("operator_identities has a duplicate")
    deployment = BoundaryDeployment(operator_identities=tuple(sorted(operator_ids)), **texts)

    exclusions_raw = _list(
        raw["threat_model_exclusions"],
        "threat_model_exclusions",
        maximum=len(THREAT_MODEL_EXCLUSIONS),
        allow_empty=False,
    )
    exclusions: list[str] = []
    for item in exclusions_raw:
        if type(item) is not str or item not in THREAT_MODEL_EXCLUSIONS:
            raise _manifest_error("threat_model_exclusions has an unknown entry")
        exclusions.append(item)
    if len(set(exclusions)) != len(exclusions):
        raise _manifest_error("threat_model_exclusions has a duplicate")
    if set(exclusions) != REQUIRED_THREAT_MODEL_EXCLUSIONS:
        raise _manifest_error("threat_model_exclusions must name every remaining risk")

    surfaces_raw = _exact_mapping(raw["surfaces"], frozenset(BOUNDARY_SURFACES), "surfaces")
    surfaces: dict[str, tuple[BoundaryEndpoint, ...]] = {}
    by_name: dict[str, BoundaryEndpoint] = {}
    for surface in BOUNDARY_SURFACES:
        items = _list(
            surfaces_raw[surface],
            f"surface {surface}",
            maximum=_MAX_ENDPOINTS_PER_SURFACE,
            allow_empty=True,
        )
        endpoints: list[BoundaryEndpoint] = []
        for index, item in enumerate(items):
            endpoint = _endpoint(item, f"surface {surface} endpoint {index}")
            if any(
                (e.scheme, e.host, e.port) == (endpoint.scheme, endpoint.host, endpoint.port)
                for e in endpoints
            ):
                raise _manifest_error(f"surface {surface} declares one destination twice")
            known = by_name.setdefault(endpoint.name, endpoint)
            if known != endpoint:
                raise _manifest_error("one endpoint name is declared with two meanings")
            endpoints.append(endpoint)
        surfaces[surface] = tuple(sorted(endpoints, key=lambda e: e.name))

    ordered_exclusions = tuple(sorted(exclusions))
    frozen_surfaces = MappingProxyType(surfaces)
    digest = hashlib.sha256(
        _canonical(boundary_id, profile, deployment, ordered_exclusions, frozen_surfaces)
    ).hexdigest()
    return PrivateBoundary(
        boundary_id, profile, deployment, ordered_exclusions, frozen_surfaces, digest
    )


def load_private_boundary(path: str | Path) -> PrivateBoundary:
    """Read and validate a boundary file with `yaml.safe_load` only."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _manifest_error("boundary manifest cannot be read") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _manifest_error("boundary manifest is not valid YAML") from exc
    return parse_private_boundary(document)


def boundary_digest(boundary: PrivateBoundary) -> str:
    """The sha256 of the canonical boundary, recomputed (never the stored field)."""
    if type(boundary) is not PrivateBoundary:
        raise InvalidBoundaryRequestError("boundary must be a PrivateBoundary")
    return hashlib.sha256(
        _canonical(
            boundary.boundary_id,
            boundary.profile,
            boundary.deployment,
            boundary.threat_model_exclusions,
            boundary.surfaces,
        )
    ).hexdigest()


# -- classification and effects --------------------------------------------


def _checked_boundary(boundary: object) -> PrivateBoundary:
    if type(boundary) is not PrivateBoundary:
        raise InvalidBoundaryRequestError("boundary must be a PrivateBoundary")
    if boundary_digest(boundary) != boundary.digest:
        raise InvalidBoundaryRequestError("boundary was altered after validation")
    return boundary


def _surface(value: object) -> str:
    if type(value) is not str or value not in BOUNDARY_SURFACES:
        raise InvalidBoundaryRequestError("surface is not a boundary surface")
    return value


def _url_parts(url: object) -> tuple[str, str, int] | None:
    """(scheme, host, port) exactly as written, or None when unparseable."""
    if type(url) is not str or not url or len(url) > 2048:
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    scheme = parsed.scheme
    host = parsed.hostname
    # `hostname` lowercases; compare against what was really written so a
    # mixed-case spelling is a different (undeclared) destination.
    netloc_host = parsed.netloc.rsplit(":", 1)[0]
    if netloc_host.startswith("[") and netloc_host.endswith("]"):
        netloc_host = netloc_host[1:-1]
    if host is None or port is None or netloc_host != host:
        return None
    return scheme, host, port


def classify_destination(
    boundary: PrivateBoundary, surface: str, url: str
) -> BoundaryClassification:
    """Internal only for an exact declared (scheme, host, port) on this surface."""
    checked = _checked_boundary(boundary)
    name = _surface(surface)
    parts = _url_parts(url)
    if parts is not None:
        for endpoint in checked.surfaces[name]:
            if (endpoint.scheme, endpoint.host, endpoint.port) == parts:
                return BoundaryClassification(name, Destination("internal"), endpoint)
    return BoundaryClassification(name, Destination("external", provider="undeclared"), None)


def endpoint_connection_policy(endpoint: BoundaryEndpoint) -> ConnectionPolicy:
    """The H006 policy for exactly one endpoint: its scheme, port and network."""
    if type(endpoint) is not BoundaryEndpoint or endpoint.scheme not in FETCH_SCHEMES:
        raise InvalidBoundaryRequestError("endpoint is not a fetchable boundary endpoint")
    return ConnectionPolicy(
        name=f"boundary-{endpoint.name}",
        allowed_schemes=frozenset({endpoint.scheme}),
        allowed_ports=frozenset({endpoint.port}),
        explicit_private_allowlist=(endpoint.network,),
    )  # ConnectionPolicy's defaults: no redirects, max_redirects 0


def _pinned_address(url: str, endpoint: BoundaryEndpoint, policy: ConnectionPolicy) -> str:
    try:
        validate_url_shape(url, policy)
        literal = normalize_host_literal(endpoint.host)
        if literal is not None:
            validate_resolved_address(endpoint.host, literal, policy)
            addresses: list[Address] = [literal]
        else:
            addresses = [
                ipaddress.ip_address(ip)
                for _family, ip in resolve_and_validate(endpoint.host, endpoint.port, policy)
            ]
    except OutboundPolicyError as exc:
        raise ResolvedOutsideBoundaryError("declared endpoint failed address validation") from exc
    except OutboundGuardError as exc:
        raise BoundaryTransportError("declared endpoint did not resolve") from exc
    for address in addresses:
        if address not in endpoint.network:
            raise ResolvedOutsideBoundaryError(
                "declared endpoint resolved outside its declared network"
            )
    return str(addresses[0])


def _headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if type(value) is not dict or len(value) > _MAX_HEADERS:
        raise InvalidBoundaryRequestError("headers must be a small dict of strings")
    for key, item in value.items():
        if type(key) is not str or type(item) is not str:
            raise InvalidBoundaryRequestError("headers must be a small dict of strings")
        if any(c in key + item for c in "\r\n\x00"):
            raise InvalidBoundaryRequestError("headers must not contain line breaks")
    return dict(value)


def _perform(
    url: str,
    endpoint: BoundaryEndpoint,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    policy = endpoint_connection_policy(endpoint)
    pinned_ip = _pinned_address(url, endpoint, policy)
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    request_headers = dict(headers)
    request_headers["Connection"] = "close"
    conn: HTTPConnection = _build_connection(
        endpoint.scheme, endpoint.host, pinned_ip, endpoint.port, timeout
    )
    try:
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        status = response.status
        response_headers = dict(response.getheaders())
    except OSError as exc:
        raise BoundaryTransportError("in-boundary connection failed") from exc
    finally:
        conn.close()
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise BoundaryTransportError("in-boundary response exceeded the size bound")
    return status, response_headers, raw


def _decide(
    boundary: PrivateBoundary,
    classification: BoundaryClassification,
    organization_profile: object,
    project_profile: object,
    job_profile: object,
    authorization: RouteAuthorization | None,
) -> EffectDecision:
    for value in (organization_profile, project_profile):
        if type(value) is not str or value not in SECURITY_PROFILE_LEVELS:
            raise InvalidBoundaryRequestError("profile is not a known security profile")
    if job_profile is not None and (
        type(job_profile) is not str or job_profile not in SECURITY_PROFILE_LEVELS
    ):
        raise InvalidBoundaryRequestError("profile is not a known security profile")
    # The boundary's own profile is a floor nobody below it can lower.
    floor = resolve_effective_security_profile(boundary.profile, organization_profile)
    request = EffectRequest(
        surface=SURFACE_TO_EFFECT_SURFACE[classification.surface],
        destination=classification.destination,
        organization_profile=floor,
        project_profile=cast(str, project_profile),
        job_profile=job_profile,
    )
    decision = enforce_effect(request, authorization)
    if classification.endpoint is None:
        raise DestinationOutsideBoundaryError(
            "the boundary fetcher performs only declared in-boundary effects"
        )
    return decision


def _fetch(
    boundary: object,
    surface: object,
    url: object,
    *,
    organization_profile: object,
    project_profile: object,
    job_profile: object,
    authorization: object,
    method: object,
    headers: object,
    body: object,
    timeout: object,
    allow_telemetry: bool,
) -> BoundaryFetchResult:
    checked = _checked_boundary(boundary)
    name = _surface(surface)
    if name == "telemetry" and not allow_telemetry:
        raise RawTelemetryRefusedError("the telemetry surface accepts only content-free events")
    if type(url) is not str:
        raise InvalidBoundaryRequestError("url must be a string")
    if authorization is not None and type(authorization) is not RouteAuthorization:
        raise InvalidBoundaryRequestError("authorization must be a RouteAuthorization")
    if type(method) is not str or method not in ("GET", "POST", "PUT"):
        raise InvalidBoundaryRequestError("method is not permitted")
    if body is not None and (type(body) is not bytes or len(body) > _MAX_BODY_BYTES):
        raise InvalidBoundaryRequestError("body must be bounded bytes")
    if type(timeout) not in (int, float) or not 0 < cast(float, timeout) <= _MAX_TIMEOUT_SECONDS:
        raise InvalidBoundaryRequestError("timeout is out of range")
    request_headers = _headers(headers)
    classification = classify_destination(checked, name, url)
    decision = _decide(
        checked, classification, organization_profile, project_profile, job_profile, authorization
    )
    # `_decide` refused every undeclared destination, so the endpoint is set.
    endpoint = cast(BoundaryEndpoint, classification.endpoint)
    status, response_headers, raw = _perform(
        url, endpoint, method, request_headers, body, cast(float, timeout)
    )
    return BoundaryFetchResult(
        name, endpoint.name, status, MappingProxyType(response_headers), raw, decision
    )


def boundary_fetch(
    boundary: PrivateBoundary,
    surface: str,
    url: str,
    *,
    organization_profile: str,
    project_profile: str,
    job_profile: str | None = None,
    authorization: RouteAuthorization | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> BoundaryFetchResult:
    """Perform one in-boundary HTTP(S) effect, or raise a coded refusal before any socket."""
    return _fetch(
        boundary,
        surface,
        url,
        organization_profile=organization_profile,
        project_profile=project_profile,
        job_profile=job_profile,
        authorization=authorization,
        method=method,
        headers=headers,
        body=body,
        timeout=timeout,
        allow_telemetry=False,
    )


def _event_json(event: TelemetryEvent) -> bytes:
    fields: dict[str, object] = {}
    for key, value in event.fields.items():
        if type(value) is ContentRef:
            fields[key] = {"ref_id": value.ref_id, "encryption_key_id": value.encryption_key_id}
        elif hasattr(value, "isoformat"):
            fields[key] = value.isoformat()
        else:
            fields[key] = value
    return json.dumps(
        {"kind": event.kind, "fields": fields}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def send_boundary_telemetry(
    boundary: PrivateBoundary,
    url: str,
    kind: str,
    fields: Mapping[str, object],
    *,
    organization_profile: str,
    project_profile: str,
    job_profile: str | None = None,
    timeout: float = 10.0,
) -> BoundaryFetchResult:
    """Validate the event with FTF-0903 first (content refused before any socket),
    then POST its canonical JSON to a declared telemetry collector."""
    event = emit_event(kind, fields)
    return _fetch(
        boundary,
        "telemetry",
        url,
        organization_profile=organization_profile,
        project_profile=project_profile,
        job_profile=job_profile,
        authorization=None,
        method="POST",
        headers={"Content-Type": "application/json"},
        body=_event_json(event),
        timeout=timeout,
        allow_telemetry=True,
    )
