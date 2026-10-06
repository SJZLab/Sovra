"""FTF-0902 -- Network-policy templates (Section 11-12, 65.4, 77.5).

This module answers a narrower question than `packages.security.url_policy`
(which governs *this process's own* outbound HTTP calls): given a typed,
versioned description of one project/tenant's workload, render the actual
Kubernetes `NetworkPolicy` manifest that a real, enforcing CNI (Section 12:
"Enforce egress with the actual network implementation, not just YAML")
must apply to isolate it -- deny-by-default in both directions, an explicit,
non-empty allowlist of internal destinations, a mandatory kube-dns carve-out
so the workload can still resolve names, and a structural, closed-set
inability to ever allow a cloud-metadata address or any IPv6 destination
(this task's own negative_acceptance: "default-open or IPv6 escape is a
release blocker").

**This module never itself proves enforcement.** Section 12 and this
task's own negative_acceptance ("NetworkPolicy YAML on a nonenforcing
cluster is not a pass") are explicit that a rendered manifest is inert
paperwork until a real CNI applies it -- see
`tests/integration/test_ftf_0902_network_policy_enforcement.py` and CI's
`network-policy-enforcement-integration` job (`.github/workflows/ci.yml`),
which stand up a real `kind` cluster twice (once with the default,
non-enforcing `kindnet` CNI, once with Calico installed) and prove the
*identical* rendered manifest from this module is silently inert on the
first cluster and actually blocks/allows real TCP connections on the
second, for the exact contrast this task's negative_acceptance asks for.

Reuses `packages.security.url_policy._CLOUD_METADATA_NETWORKS` (a real CIDR
overlap check against it, not a probe of a few concrete addresses) as this
module's own single source of truth for "what counts as a cloud-metadata
identity" (Section 65.4's own canonical AWS/GCP/Azure/DigitalOcean/Alibaba
IMDS addresses) rather than re-deriving a second, possibly-drifting list --
FTF-H006's module already owns that identity for this process's own
outbound calls, and this module targets the same real addresses for a
workload's *inbound-to-the-workload's-own-egress* case.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from typing import Literal, cast

import yaml

from packages.security.url_policy import _CLOUD_METADATA_NETWORKS

__all__ = [
    "NetworkPolicyTemplateError",
    "NetworkPolicyDefaultOpenError",
    "MetadataEndpointExposedError",
    "IPv6EscapeError",
    "MissingExplicitScopingError",
    "InvalidEndpointDefinitionError",
    "PublicCidrEndpointError",
    "PortRule",
    "CidrEndpoint",
    "PodSelectorEndpoint",
    "AllowedInternalEndpoint",
    "NetworkPolicyTemplate",
    "validate_network_policy_template",
    "render_network_policy_manifest",
    "render_network_policy_yaml",
    "manifest_digest",
    "KUBE_DNS_NAMESPACE_LABELS",
    "KUBE_DNS_POD_LABELS",
]


class NetworkPolicyTemplateError(Exception):
    """Base class for every real rejection this module can raise. Never
    caught and converted to a rendered manifest -- see
    `render_network_policy_manifest`, which always re-validates first."""


class NetworkPolicyDefaultOpenError(NetworkPolicyTemplateError):
    """The template does not deny-by-default in at least one direction --
    this task's own negative_acceptance: "default-open ... is a release
    blocker." Raised for `deny_by_default_egress=False`,
    `deny_by_default_ingress=False`, or an allowed endpoint whose CIDR is
    the full-open `0.0.0.0/0`/`::/0` (a wildcard endpoint defeats
    deny-by-default exactly as much as the flag being off)."""


class MetadataEndpointExposedError(NetworkPolicyTemplateError):
    """`block_metadata_endpoint=False`, or an allowed endpoint's CIDR
    overlaps a real cloud-metadata network
    (`packages.security.url_policy._CLOUD_METADATA_NETWORKS`) --
    Section 65.4's own metadata carve-out, applied here to a workload's
    outbound allowlist rather than this process's own."""


class IPv6EscapeError(NetworkPolicyTemplateError):
    """This task's own negative_acceptance: "IPv6 escape is a release
    blocker." Release 1 ships no supported way to allow any IPv6
    destination at all -- `ipv6_policy` is validated at runtime to be
    exactly `"blocked"` (a caller building this dataclass from untrusted
    deserialized data, not through the type checker, could otherwise pass
    any string), and any allowed endpoint naming an IPv6 CIDR is refused
    outright rather than silently rendered."""


class MissingExplicitScopingError(NetworkPolicyTemplateError):
    """This task's own negative_acceptance: "rejects one missing explicit
    endpoint scoping." Raised when `allowed_internal_endpoints` is `None`
    (not yet configured -- distinct from an explicit, deliberate empty
    tuple, which is a legitimate "no internal endpoints needed" workload),
    when `pod_selector` is empty (an empty Kubernetes `podSelector` matches
    *every* pod in the namespace -- a real, well-documented Kubernetes
    semantics trap this validator refuses to let a caller trigger by
    omission), or when `namespace`/`organization_id`/`template_id` is
    empty."""


class InvalidEndpointDefinitionError(NetworkPolicyTemplateError):
    """A structurally malformed endpoint: an unparseable CIDR, an empty
    `ports` tuple (this module never renders an "all ports" allow rule --
    every explicit endpoint names its exact allowed ports), a port/
    protocol outside the real, valid ranges, or (at render time only) an
    `allowed_internal_endpoints` entry that is not *exactly* `CidrEndpoint`
    or `PodSelectorEndpoint` -- a subclass or duck-typed object is refused
    even though it would satisfy an `isinstance` check, since a subclass
    can override `__post_init__` to skip every invariant below."""


class PublicCidrEndpointError(NetworkPolicyTemplateError):
    """Control-plane decision (2026-10-04T11:00Z review): an explicit
    internal endpoint names a real internal address, never a public one --
    `CidrEndpoint.cidr` must be a subnet of an RFC1918 private range
    (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). Checked after the
    metadata-overlap check (a metadata address is reported as
    `MetadataEndpointExposedError`, its own more specific identity, even
    though it is also non-RFC1918)."""


_PROTOCOLS: frozenset[str] = frozenset({"TCP", "UDP"})

# Matches component_binding_schema.py's own NETWORK_POLICY_IDENTITY_MAX_LENGTH.
TEMPLATE_ID_MAX_LENGTH = 200

# Control-plane decision (2026-10-04T11:00Z review): an allowed internal
# endpoint's CIDR must be a real private address, never a public one.
# IPv6 (including ULA, fc00::/7) is out of scope here -- every IPv6 CIDR is
# already refused outright by IPv6EscapeError before this is ever checked.
_RFC1918_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


def _exact_str(value: object, what: str, *, empty_error: type[Exception]) -> str:
    """`value` must be *exactly* `str` (never a subclass, whose `__str__`/
    `__eq__`/`__bool__` can lie to every check that follows) and non-empty."""
    if type(value) is not str:
        raise InvalidEndpointDefinitionError(f"{what} must be exactly a str, got {type(value)!r}")
    if not value:
        raise empty_error(f"{what} must be a non-empty string")
    return value


def _normalize_label_map(value: object, what: str) -> dict[str, str]:
    """A fresh `dict[str, str]` rebuilt from `value`, which must be exactly a
    non-empty `dict` whose keys and values are exactly non-empty `str`. The
    returned dict is what gets rendered -- never the caller's own object."""
    if type(value) is not dict:
        raise InvalidEndpointDefinitionError(f"{what} must be exactly a dict, got {type(value)!r}")
    if not value:
        raise MissingExplicitScopingError(
            f"{what} is empty -- an empty Kubernetes selector matches every pod in the "
            "namespace, which is never an explicit scoping"
        )
    labels: dict[str, str] = {}
    for key, label_value in value.items():
        labels[_exact_str(key, f"{what} key", empty_error=InvalidEndpointDefinitionError)] = (
            _exact_str(
                label_value, f"{what} value for {key!r}", empty_error=InvalidEndpointDefinitionError
            )
        )
    return labels


def _normalize_ports(ports: object, description: str) -> list[dict[str, str | int]]:
    """Re-derives the exact rendered `ports` list from `ports`, which must be
    exactly a non-empty `tuple` of exactly `PortRule`, each with an exact
    `str` protocol in the allowed set and an exact `int` port in 1-65535.
    Independent of `PortRule.__post_init__` having run (or of a subclass /
    `object.__setattr__` having skipped it); the result is non-empty by
    construction, so there is never an "all ports" `ports: []` rule."""
    if type(ports) is not tuple:
        raise InvalidEndpointDefinitionError(
            f"endpoint {description!r} ports must be exactly a tuple, got {type(ports)!r}"
        )
    if not ports:
        raise InvalidEndpointDefinitionError(
            f"endpoint {description!r} must name at least one explicit port -- "
            "an endpoint with no ports is not a representable allow rule"
        )
    rendered: list[dict[str, str | int]] = []
    for rule in ports:
        if type(rule) is not PortRule:
            raise InvalidEndpointDefinitionError(
                f"endpoint {description!r} ports must be exactly PortRule, got {type(rule)!r}"
            )
        protocol, port = rule.protocol, rule.port
        if type(protocol) is not str or protocol not in _PROTOCOLS:
            raise InvalidEndpointDefinitionError(
                f"protocol must be exactly one of {sorted(_PROTOCOLS)}, got {protocol!r}"
            )
        if type(port) is not int or not (1 <= port <= 65535):
            raise InvalidEndpointDefinitionError(f"port must be an exact int 1-65535, got {port!r}")
        rendered.append({"protocol": protocol, "port": port})
    return rendered


def _validate_cidr_endpoint_fields(
    description: object, cidr: object, ports: object
) -> tuple[str, list[dict[str, str | int]]]:
    """Every real invariant a `CidrEndpoint`'s three fields must satisfy,
    independent of *how* those values came to be on the instance --  a
    normal constructor call, a caller that bypassed `__post_init__` via
    `object.__setattr__` on an already-frozen instance, or a subclass that
    overrides `__post_init__` to skip it entirely. Called once at
    construction time (`CidrEndpoint.__post_init__`) and once again,
    independently, against the endpoint's own *current* attribute values
    at render time (`_endpoint_egress_rule`).

    Types are exact (`type(x) is str`, never `isinstance`): a `str` subclass
    can report one value from `__str__` (which `ipaddress` parses) and
    another from everything else. Returns the *normalized* values to emit --
    `str(ip_network(cidr))` and a rebuilt, non-empty ports list -- so the
    renderer never emits a field it did not itself just validate.
    """
    description = _exact_str(description, "description", empty_error=InvalidEndpointDefinitionError)
    rendered_ports = _normalize_ports(ports, description)
    if type(cidr) is not str:
        raise InvalidEndpointDefinitionError(
            f"endpoint {description!r} cidr must be exactly a str, got {type(cidr)!r}"
        )
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except (ValueError, TypeError) as exc:
        raise InvalidEndpointDefinitionError(
            f"endpoint {description!r} has an unparseable cidr {cidr!r}: {exc}"
        ) from exc
    if network.prefixlen == 0:
        raise NetworkPolicyDefaultOpenError(
            f"endpoint {description!r} allows the wildcard network {cidr!r} "
            "(prefix length 0) -- this defeats deny-by-default exactly as much as "
            "turning deny_by_default_egress off"
        )
    if isinstance(network, ipaddress.IPv6Network):
        raise IPv6EscapeError(
            f"endpoint {description!r} names an IPv6 cidr {cidr!r} -- "
            "Release 1 ships no supported way to allow any IPv6 destination"
        )
    # Metadata carve-out: derived directly from url_policy's own real,
    # canonical network list (FTF-H006's single source of truth for "what
    # counts as a cloud-metadata identity") via a real CIDR-overlap check,
    # rather than probing a hardcoded handful of concrete addresses that
    # could drift from that list over time.
    for metadata_network in _CLOUD_METADATA_NETWORKS:
        if metadata_network.version == network.version and network.overlaps(metadata_network):
            raise MetadataEndpointExposedError(
                f"endpoint {description!r}'s cidr {cidr!r} overlaps the real "
                f"cloud-metadata network {metadata_network} -- never permitted, "
                "regardless of an operator's explicit allowlist intent"
            )
    if not any(network.subnet_of(rfc1918) for rfc1918 in _RFC1918_NETWORKS):
        raise PublicCidrEndpointError(
            f"endpoint {description!r}'s cidr {cidr!r} is not inside an RFC1918 "
            "private range (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) -- an "
            "explicit internal endpoint must name a real internal address, never "
            "a public or otherwise-special one"
        )
    return str(network), rendered_ports


def _validate_pod_selector_endpoint_fields(
    description: object, namespace: object, pod_labels: object, ports: object
) -> tuple[str, dict[str, str], list[dict[str, str | int]]]:
    """As `_validate_cidr_endpoint_fields`, for a `PodSelectorEndpoint`'s
    four fields. Returns the normalized `(namespace, labels, ports)` to
    emit -- fresh objects, never the caller's own."""
    description = _exact_str(description, "description", empty_error=InvalidEndpointDefinitionError)
    if type(namespace) is str and not namespace:
        raise MissingExplicitScopingError(f"endpoint {description!r} has an empty namespace")
    namespace = _exact_str(
        namespace, f"endpoint {description!r} namespace", empty_error=MissingExplicitScopingError
    )
    labels = _normalize_label_map(pod_labels, f"endpoint {description!r} pod_labels")
    return namespace, labels, _normalize_ports(ports, description)


@dataclass(frozen=True)
class PortRule:
    """One explicit `(protocol, port)` pair. There is no "all ports"
    representation anywhere in this module -- an endpoint's `ports` tuple
    is the complete, exact allowlist for that destination."""

    protocol: Literal["TCP", "UDP"]
    port: int

    def __post_init__(self) -> None:
        _normalize_ports((self,), "PortRule")


@dataclass(frozen=True)
class CidrEndpoint:
    """An explicitly allowed internal destination named by CIDR -- for a
    real internal service reached by a stable ClusterIP/VIP (e.g. an
    internal load balancer or a service mesh VIP) rather than by
    Kubernetes' own pod/namespace selectors. `description` is a real,
    non-empty, content-free identity for evidence records (Section 12:
    "Qualification evidence records ... attempted routes, blocked/allowed
    destinations"), never a raw secret or tenant data value.
    """

    description: str
    cidr: str
    ports: tuple[PortRule, ...]

    def __post_init__(self) -> None:
        _validate_cidr_endpoint_fields(self.description, self.cidr, self.ports)


@dataclass(frozen=True)
class PodSelectorEndpoint:
    """An explicitly allowed internal destination named by Kubernetes'
    own namespace+pod selectors -- for another in-cluster service reached
    by real pod-to-pod networking (Section 77.5's own MLflow/Argilla/
    LiteLLM/Temporal component bindings), where a stable CIDR does not
    exist because pod IPs are ephemeral.
    """

    description: str
    namespace: str
    pod_labels: dict[str, str]
    ports: tuple[PortRule, ...]

    def __post_init__(self) -> None:
        _validate_pod_selector_endpoint_fields(
            self.description, self.namespace, self.pod_labels, self.ports
        )


AllowedInternalEndpoint = CidrEndpoint | PodSelectorEndpoint

# CoreDNS's own real, standard labels (present on every stock Kubernetes
# 1.21+ cluster) -- the one egress destination this module allows besides
# a template's own explicit endpoints, since a workload with zero DNS
# egress cannot resolve even the internal service names its own allowed
# endpoints might use.
KUBE_DNS_NAMESPACE_LABELS: dict[str, str] = {"kubernetes.io/metadata.name": "kube-system"}
KUBE_DNS_POD_LABELS: dict[str, str] = {"k8s-app": "kube-dns"}


@dataclass(frozen=True)
class NetworkPolicyTemplate:
    """One project/tenant-scoped network-policy template. Frozen and
    versioned (`schema_version`) -- Section 77.5's own `ComponentBinding`/
    `ComponentVersionLock` pattern of real, exact, non-`latest` identity
    applied to this task's own domain object.
    """

    schema_version: int
    template_id: str
    organization_id: str
    project_id: str | None
    namespace: str
    pod_selector: dict[str, str]
    cni_profile: Literal["calico", "cilium"]
    deny_by_default_egress: bool
    deny_by_default_ingress: bool
    block_metadata_endpoint: bool
    ipv6_policy: Literal["blocked"]
    allowed_internal_endpoints: tuple[AllowedInternalEndpoint, ...] | None
    allow_dns: bool = True


def _validated_template_identity(
    template: NetworkPolicyTemplate,
) -> tuple[str, str, str, dict[str, str]]:
    """Exact-type, non-empty `(template_id, organization_id, namespace,
    pod_selector)`, with `pod_selector` rebuilt as a fresh dict -- the only
    values the renderer ever emits for the template's own identity."""
    template_id = template.template_id
    if type(template_id) is not str or not template_id or len(template_id) > TEMPLATE_ID_MAX_LENGTH:
        raise MissingExplicitScopingError(
            f"template_id must be a non-empty str of at most {TEMPLATE_ID_MAX_LENGTH} "
            f"characters, got {template_id!r}"
        )
    organization_id = _exact_str(
        template.organization_id, "organization_id", empty_error=MissingExplicitScopingError
    )
    namespace = _exact_str(template.namespace, "namespace", empty_error=MissingExplicitScopingError)
    pod_selector = _normalize_label_map(template.pod_selector, "template pod_selector")
    return template_id, organization_id, namespace, pod_selector


def validate_network_policy_template(template: NetworkPolicyTemplate) -> NetworkPolicyTemplate:
    """Raises a specific `NetworkPolicyTemplateError` subclass for every
    real invariant this task's acceptance/negative_acceptance names.
    Returns `template` unchanged on success (every field, including each
    endpoint, has already validated itself in `__post_init__` -- this
    function's own job is the cross-field/template-level invariants no
    single dataclass's constructor can see)."""
    _validated_template_identity(template)
    if template.deny_by_default_egress is not True:
        raise NetworkPolicyDefaultOpenError(
            f"template {template.template_id!r} has deny_by_default_egress=False"
        )
    if template.deny_by_default_ingress is not True:
        raise NetworkPolicyDefaultOpenError(
            f"template {template.template_id!r} has deny_by_default_ingress=False"
        )
    if template.block_metadata_endpoint is not True:
        raise MetadataEndpointExposedError(
            f"template {template.template_id!r} has block_metadata_endpoint=False"
        )
    if type(template.ipv6_policy) is not str or template.ipv6_policy != "blocked":
        # Only reachable from untrusted deserialized data bypassing the
        # type checker -- the Literal type offers no other value to
        # construct through normal Python code.
        raise IPv6EscapeError(
            f"template {template.template_id!r} has ipv6_policy={template.ipv6_policy!r}, "
            "not 'blocked' -- Release 1 has no supported way to allow IPv6 egress"
        )
    if template.allowed_internal_endpoints is None:
        raise MissingExplicitScopingError(
            f"template {template.template_id!r} has allowed_internal_endpoints=None -- "
            "an explicit tuple (possibly empty, for a workload that genuinely needs no "
            "internal endpoints) is required; None means 'not yet configured'"
        )
    if type(template.allowed_internal_endpoints) is not tuple:
        raise InvalidEndpointDefinitionError(
            "allowed_internal_endpoints must be exactly a tuple, got "
            f"{type(template.allowed_internal_endpoints)!r}"
        )
    # Endpoint-level invariants are not re-checked here: a template-level
    # mutation (e.g. deny_by_default_egress flipped after construction) is
    # caught above every time this function runs, but an *endpoint* bypass
    # (object.__setattr__ on a frozen CidrEndpoint/PodSelectorEndpoint, a
    # subclass overriding __post_init__, or a duck-typed stand-in) can only
    # be caught by re-deriving the egress rule from that endpoint's own
    # current values -- done independently, every render, by
    # `_endpoint_egress_rule` inside `_endpoint_egress_rule`.
    return template


def _endpoint_egress_rule(endpoint: object) -> dict:
    """Re-runs every real per-endpoint invariant against `endpoint`'s own
    *current* attribute values, independent of whatever validation ran (or
    did not run) at construction time, and builds the egress rule *only*
    from the normalized values that validation returns -- never from the
    endpoint's own attributes. This is what lets
    `render_network_policy_manifest`'s "always re-validates" claim hold
    against a mutated, subclassed, duck-typed or type-lying endpoint.

    Accepts *exactly* `CidrEndpoint` or `PodSelectorEndpoint` -- checked by
    `type(endpoint) is ...`, never `isinstance`, since a subclass that
    overrides `__post_init__` to skip every check would still satisfy an
    `isinstance` check. Anything else is refused outright.

    No `except` clause on the ipBlock: the real Kubernetes API rejects an
    `except` not inside `cidr`, and validation guarantees `cidr` never
    contains a cloud-metadata address (or is IPv6), so there is never a
    sub-range to carve back out. The egress list is allow-only (Section 12):
    an address is reachable iff it appears in one of these `to` blocks.
    """
    endpoint_type = type(endpoint)
    if endpoint_type is CidrEndpoint:
        cidr_endpoint = cast(CidrEndpoint, endpoint)
        cidr, ports = _validate_cidr_endpoint_fields(
            cidr_endpoint.description, cidr_endpoint.cidr, cidr_endpoint.ports
        )
        return {"to": [{"ipBlock": {"cidr": cidr}}], "ports": ports}
    if endpoint_type is PodSelectorEndpoint:
        pod_endpoint = cast(PodSelectorEndpoint, endpoint)
        namespace, labels, ports = _validate_pod_selector_endpoint_fields(
            pod_endpoint.description,
            pod_endpoint.namespace,
            pod_endpoint.pod_labels,
            pod_endpoint.ports,
        )
        return {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": namespace}
                    },
                    "podSelector": {"matchLabels": labels},
                }
            ],
            "ports": ports,
        }
    raise InvalidEndpointDefinitionError(
        f"allowed_internal_endpoints contains a {endpoint_type!r} instance -- only the "
        "exact CidrEndpoint or PodSelectorEndpoint types are ever rendered; a subclass or "
        "other duck-typed object is refused even if it would satisfy isinstance()"
    )


def render_network_policy_manifest(template: NetworkPolicyTemplate) -> dict:
    """Renders the one real Kubernetes `NetworkPolicy` manifest for
    `template` as a plain dict (the real `networking.k8s.io/v1` API
    shape). Always re-validates first -- there is no code path in this
    module that can render a manifest for a template
    `validate_network_policy_template` would refuse.

    `policyTypes: [Ingress, Egress]` with an empty `ingress: []` denies
    all inbound traffic to the selected pods; `egress` lists exactly the
    mandatory kube-dns rule (if `allow_dns`) plus one rule per explicit
    allowed endpoint -- nothing else is reachable, which *is* this
    template's deny-by-default egress posture (an explicit, non-empty
    `egress` list under `policyTypes: [Egress]` means "only this", not
    "this plus whatever else").
    """
    validate_network_policy_template(template)
    template_id, organization_id, namespace, pod_selector = _validated_template_identity(template)

    egress_rules: list[dict] = []
    if template.allow_dns:
        egress_rules.append(
            {
                "to": [
                    {
                        "namespaceSelector": {"matchLabels": KUBE_DNS_NAMESPACE_LABELS},
                        "podSelector": {"matchLabels": KUBE_DNS_POD_LABELS},
                    }
                ],
                "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
            }
        )
    assert template.allowed_internal_endpoints is not None  # validated above
    for endpoint in template.allowed_internal_endpoints:
        egress_rules.append(_endpoint_egress_rule(endpoint))

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": template_id,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sovra-network-policy-templates",
                "sovra.io/organization-id": organization_id,
            },
        },
        "spec": {
            "podSelector": {"matchLabels": pod_selector},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": egress_rules,
        },
    }


def render_network_policy_yaml(template: NetworkPolicyTemplate) -> str:
    """As `render_network_policy_manifest`, serialized to real YAML text --
    the literal "NetworkPolicy YAML" this task's negative_acceptance
    names, ready to `kubectl apply -f -`."""
    return yaml.safe_dump(render_network_policy_manifest(template), sort_keys=False)


def manifest_digest(manifest: dict) -> str:
    """A stable sha256 identity over `manifest`'s canonical JSON form --
    Section 77.5's own "configuration digest" pattern applied to a
    rendered policy, for evidence records that must bind to the exact
    policy content actually applied (Section 12: "Qualification evidence
    records topology, policy digest ...")."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
