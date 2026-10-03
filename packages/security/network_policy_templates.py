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

Reuses `packages.security.url_policy.is_cloud_metadata_address` as this
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
from typing import Literal

import yaml

from packages.security.url_policy import is_cloud_metadata_address

__all__ = [
    "NetworkPolicyTemplateError",
    "NetworkPolicyDefaultOpenError",
    "MetadataEndpointExposedError",
    "IPv6EscapeError",
    "MissingExplicitScopingError",
    "InvalidEndpointDefinitionError",
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
    contains a real cloud-metadata address
    (`packages.security.url_policy.is_cloud_metadata_address`) --
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
    every explicit endpoint names its exact allowed ports), or a port/
    protocol outside the real, valid ranges."""


_PROTOCOLS: frozenset[str] = frozenset({"TCP", "UDP"})

# Matches component_binding_schema.py's own NETWORK_POLICY_IDENTITY_MAX_LENGTH.
TEMPLATE_ID_MAX_LENGTH = 200


@dataclass(frozen=True)
class PortRule:
    """One explicit `(protocol, port)` pair. There is no "all ports"
    representation anywhere in this module -- an endpoint's `ports` tuple
    is the complete, exact allowlist for that destination."""

    protocol: Literal["TCP", "UDP"]
    port: int

    def __post_init__(self) -> None:
        if self.protocol not in _PROTOCOLS:
            raise InvalidEndpointDefinitionError(
                f"protocol must be one of {sorted(_PROTOCOLS)}, got {self.protocol!r}"
            )
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not (1 <= self.port <= 65535)
        ):
            raise InvalidEndpointDefinitionError(
                f"port must be an integer 1-65535, got {self.port!r}"
            )


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
        if not self.description or not isinstance(self.description, str):
            raise InvalidEndpointDefinitionError("description must be a non-empty string")
        if not self.ports:
            raise InvalidEndpointDefinitionError(
                f"endpoint {self.description!r} must name at least one explicit port -- "
                "an endpoint with no ports is not a representable allow rule"
            )
        try:
            network = ipaddress.ip_network(self.cidr, strict=True)
        except ValueError as exc:
            raise InvalidEndpointDefinitionError(
                f"endpoint {self.description!r} has an unparseable cidr {self.cidr!r}: {exc}"
            ) from exc
        if network.prefixlen == 0:
            raise NetworkPolicyDefaultOpenError(
                f"endpoint {self.description!r} allows the wildcard network {self.cidr!r} "
                "(prefix length 0) -- this defeats deny-by-default exactly as much as "
                "turning deny_by_default_egress off"
            )
        if isinstance(network, ipaddress.IPv6Network):
            raise IPv6EscapeError(
                f"endpoint {self.description!r} names an IPv6 cidr {self.cidr!r} -- "
                "Release 1 ships no supported way to allow any IPv6 destination"
            )
        # Metadata carve-out: reused from packages.security.url_policy so
        # this module never drifts from FTF-H006's own canonical identity
        # list. ip_network has no API to enumerate "every address this
        # predicate recognizes", so this tests each of the concrete
        # addresses that function's own docstring documents.
        for probe in (
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("100.100.100.200"),
            ipaddress.ip_address("fd00:ec2::254"),
        ):
            same_family_and_contained = probe.version == network.version and probe in network
            if same_family_and_contained and is_cloud_metadata_address(probe):
                raise MetadataEndpointExposedError(
                    f"endpoint {self.description!r}'s cidr {self.cidr!r} contains the "
                    f"real cloud-metadata address {probe} -- never permitted, regardless "
                    "of an operator's explicit allowlist intent"
                )


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
        if not self.description or not isinstance(self.description, str):
            raise InvalidEndpointDefinitionError("description must be a non-empty string")
        if not self.namespace:
            raise MissingExplicitScopingError(
                f"endpoint {self.description!r} has an empty namespace"
            )
        if not self.pod_labels:
            raise MissingExplicitScopingError(
                f"endpoint {self.description!r} has an empty pod_labels selector -- an "
                "empty Kubernetes podSelector matches every pod in the namespace, which "
                "is never an explicit internal-endpoint scoping"
            )
        if not self.ports:
            raise InvalidEndpointDefinitionError(
                f"endpoint {self.description!r} must name at least one explicit port"
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


def validate_network_policy_template(template: NetworkPolicyTemplate) -> NetworkPolicyTemplate:
    """Raises a specific `NetworkPolicyTemplateError` subclass for every
    real invariant this task's acceptance/negative_acceptance names.
    Returns `template` unchanged on success (every field, including each
    endpoint, has already validated itself in `__post_init__` -- this
    function's own job is the cross-field/template-level invariants no
    single dataclass's constructor can see)."""
    if not template.template_id or len(template.template_id) > TEMPLATE_ID_MAX_LENGTH:
        raise MissingExplicitScopingError(
            f"template_id must be a non-empty string of at most {TEMPLATE_ID_MAX_LENGTH} "
            f"characters, got {template.template_id!r}"
        )
    if not template.organization_id:
        raise MissingExplicitScopingError("organization_id must be a non-empty string")
    if not template.namespace:
        raise MissingExplicitScopingError("namespace must be a non-empty string")
    if not template.pod_selector:
        raise MissingExplicitScopingError(
            "pod_selector must be non-empty -- an empty Kubernetes podSelector matches "
            "every pod in the namespace, which is never an explicit scoping"
        )
    if not template.deny_by_default_egress:
        raise NetworkPolicyDefaultOpenError(
            f"template {template.template_id!r} has deny_by_default_egress=False"
        )
    if not template.deny_by_default_ingress:
        raise NetworkPolicyDefaultOpenError(
            f"template {template.template_id!r} has deny_by_default_ingress=False"
        )
    if not template.block_metadata_endpoint:
        raise MetadataEndpointExposedError(
            f"template {template.template_id!r} has block_metadata_endpoint=False"
        )
    if template.ipv6_policy != "blocked":
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
    # Each endpoint already validated itself in __post_init__ at
    # construction time; re-running the same checks here would be inert
    # (frozen dataclasses cannot be mutated after construction) and is
    # deliberately not repeated.
    return template


def _endpoint_egress_rule(endpoint: AllowedInternalEndpoint) -> dict:
    ports = [{"protocol": rule.protocol, "port": rule.port} for rule in endpoint.ports]
    if isinstance(endpoint, CidrEndpoint):
        # No `except` clause here: the real Kubernetes API rejects an
        # ipBlock whose `except` values are not themselves inside `cidr`
        # (kubernetes/kubernetes's own networking/v1 validation), and
        # CidrEndpoint.__post_init__ already guarantees `cidr` never
        # contains a real cloud-metadata address (or is IPv6 at all) --
        # so there is never a metadata sub-range of this endpoint's own
        # cidr to carve back out. Metadata/IPv6 are blocked the same way
        # every other non-allow-listed destination is: this module's
        # egress list is allow-only (Section 12), so an address is
        # reachable if and only if it appears in one of these `to` blocks
        # at all -- there is no broader "allow, except ..." rule for a
        # narrower carve-out to ever be needed against.
        return {"to": [{"ipBlock": {"cidr": endpoint.cidr}}], "ports": ports}
    return {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": endpoint.namespace}
                },
                "podSelector": {"matchLabels": dict(endpoint.pod_labels)},
            }
        ],
        "ports": ports,
    }


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
            "name": template.template_id,
            "namespace": template.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "sovra-network-policy-templates",
                "sovra.io/organization-id": template.organization_id,
            },
        },
        "spec": {
            "podSelector": {"matchLabels": dict(template.pod_selector)},
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
