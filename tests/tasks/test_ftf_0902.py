#!/usr/bin/env python3
"""FTF-0902 -- Network-policy templates (no-DB, no-external-egress checks).

Real, live-enforcement evidence that a rendered manifest actually blocks/
allows real traffic on a real cluster with a real CNI -- this task's own
acceptance clause ("Install and test actual CNI/firewall/DNS/IPv6/metadata
restrictions") and negative_acceptance ("NetworkPolicy YAML on a
nonenforcing cluster is not a pass") -- is
`tests/integration/test_ftf_0902_network_policy_enforcement.py`, run in
CI's dedicated `network-policy-enforcement-integration` job
(`.github/workflows/ci.yml`), which stands up two real, disposable `kind`
clusters (kindnet's default non-enforcing CNI, then Calico) and proves the
identical rendered manifest is inert on the first and actually enforces on
the second -- not re-executable here (no Docker/kind in this sandbox, and
even where Docker exists a real CNI's admission/enforcement behavior is
not something a pure-Python unit suite can fake without becoming exactly
the "canned pass" this task's own negative_acceptance forbids).

This suite proves, without any cluster:

1. Every one of this task's own negative_acceptance invariants is a real,
   independently raised, specifically-typed exception -- not a single
   generic `ValueError`, and not silently accepted:
   `NetworkPolicyDefaultOpenError` (deny_by_default_egress/ingress=False,
   or a wildcard `0.0.0.0/0` endpoint), `MetadataEndpointExposedError`
   (block_metadata_endpoint=False, or an endpoint CIDR containing a real
   cloud-metadata address), `IPv6EscapeError` (an IPv6 endpoint CIDR, or
   `ipv6_policy` forced to anything but `"blocked"` by bypassing the type
   checker), `MissingExplicitScopingError` (`allowed_internal_endpoints`
   left `None`, an empty `pod_selector`, or an endpoint's own empty
   `pod_labels`/blank `namespace`), and `InvalidEndpointDefinitionError`
   (an endpoint with no explicit ports, or an unparseable CIDR).
2. Positive/boundary cases: a real, valid template (mixing a `CidrEndpoint`
   and a `PodSelectorEndpoint`) validates and renders a real
   `networking.k8s.io/v1` NetworkPolicy manifest with the exact structure
   Section 12 requires (deny-all ingress, an explicit kube-dns egress
   rule, one egress rule per allowed endpoint, nothing else) -- including
   the boundary that a CIDR immediately adjacent to a real metadata
   address (not containing it) is accepted, and that a deliberately empty
   `allowed_internal_endpoints` tuple (a fully closed workload) is a valid,
   different case from `None`.
3. `render_network_policy_manifest` always re-validates internally, even
   against a template object a caller mutated after construction to bypass
   its own dataclass `__post_init__` checks -- proving the "always
   validated" claim in this module's own docstring rather than merely
   its type hints.
4. YAML round-trip and digest sensitivity: `render_network_policy_yaml`
   parses back to the identical dict `render_network_policy_manifest`
   returns; `manifest_digest` is deterministic for the same manifest and
   changes for a materially different one.
5. Real fault injection (Law 1): a regex-based mutation of the real
   on-disk module (survives `ruff format` reflow, unlike a literal-text
   match) removes the `deny_by_default_egress` check from
   `validate_network_policy_template`, run in a fresh subprocess against a
   template with `deny_by_default_egress=False` -- the mutated module
   genuinely accepts it (proving the removed check was load-bearing, not
   decorative), then the file is restored byte-identical (confirmed by a
   direct bytes comparison, not merely re-running the suite).

Run directly: python3 tests/tasks/test_ftf_0902.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.security.network_policy_templates import (  # noqa: E402
    KUBE_DNS_NAMESPACE_LABELS,
    KUBE_DNS_POD_LABELS,
    CidrEndpoint,
    InvalidEndpointDefinitionError,
    IPv6EscapeError,
    MetadataEndpointExposedError,
    MissingExplicitScopingError,
    NetworkPolicyDefaultOpenError,
    NetworkPolicyTemplate,
    NetworkPolicyTemplateError,
    PodSelectorEndpoint,
    PortRule,
    PublicCidrEndpointError,
    manifest_digest,
    render_network_policy_manifest,
    render_network_policy_yaml,
    validate_network_policy_template,
)
from packages.security.url_policy import _CLOUD_METADATA_NETWORKS  # noqa: E402

MODULE_PATH = ROOT / "packages" / "security" / "network_policy_templates.py"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(exc_type: type[Exception], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


def _base_template(**overrides: object) -> NetworkPolicyTemplate:
    defaults: dict[str, object] = dict(
        schema_version=1,
        template_id="proj-abc123-training-worker",
        organization_id="org-abc123",
        project_id="proj-abc123",
        namespace="sovra-proj-abc123",
        pod_selector={"app": "training-worker"},
        cni_profile="calico",
        deny_by_default_egress=True,
        deny_by_default_ingress=True,
        block_metadata_endpoint=True,
        ipv6_policy="blocked",
        allowed_internal_endpoints=(
            CidrEndpoint(
                description="internal-postgres-clusterip",
                cidr="10.96.0.10/32",
                ports=(PortRule("TCP", 5432),),
            ),
            PodSelectorEndpoint(
                description="in-cluster-mlflow",
                namespace="sovra-shared",
                pod_labels={"app": "mlflow"},
                ports=(PortRule("TCP", 5000),),
            ),
        ),
        allow_dns=True,
    )
    defaults.update(overrides)
    return NetworkPolicyTemplate(**defaults)  # type: ignore[arg-type]


def test_port_rule_validates_protocol_and_range() -> None:
    check("FTF-0902", "TCP port 1 is accepted", PortRule("TCP", 1).port == 1)
    check("FTF-0902", "UDP port 65535 is accepted", PortRule("UDP", 65535).port == 65535)
    check(
        "FTF-0902",
        "an unrecognized protocol is refused",
        _rejects(InvalidEndpointDefinitionError, PortRule, "ICMP", 80),
    )
    check(
        "FTF-0902",
        "port 0 is refused",
        _rejects(InvalidEndpointDefinitionError, PortRule, "TCP", 0),
    )
    check(
        "FTF-0902",
        "port 65536 is refused",
        _rejects(InvalidEndpointDefinitionError, PortRule, "TCP", 65536),
    )
    check(
        "FTF-0902",
        "a bool is refused as a port (bool is a subclass of int in Python)",
        _rejects(InvalidEndpointDefinitionError, PortRule, "TCP", True),
    )


def test_cidr_endpoint_rejects_wildcard_default_open() -> None:
    # negative_acceptance: "default-open ... is a release blocker" --
    # a wildcard endpoint defeats deny-by-default exactly like the flag.
    check(
        "FTF-0902",
        "an endpoint allowing 0.0.0.0/0 is refused as default-open",
        _rejects(
            NetworkPolicyDefaultOpenError,
            CidrEndpoint,
            description="everything",
            cidr="0.0.0.0/0",
            ports=(PortRule("TCP", 443),),
        ),
    )


def test_cidr_endpoint_rejects_ipv6() -> None:
    # negative_acceptance: "IPv6 escape is a release blocker" -- Release 1
    # offers no way to allow any IPv6 destination at all.
    check(
        "FTF-0902",
        "an endpoint naming an IPv6 cidr is refused",
        _rejects(
            IPv6EscapeError,
            CidrEndpoint,
            description="ipv6-internal",
            cidr="2001:db8::/32",
            ports=(PortRule("TCP", 443),),
        ),
    )


def test_cidr_endpoint_rejects_metadata_overlap_and_accepts_adjacent() -> None:
    check(
        "FTF-0902",
        "an endpoint whose exact cidr is the AWS/GCP/Azure metadata address is refused",
        _rejects(
            MetadataEndpointExposedError,
            CidrEndpoint,
            description="metadata-exact",
            cidr="169.254.169.254/32",
            ports=(PortRule("TCP", 80),),
        ),
    )
    check(
        "FTF-0902",
        "a broad link-local allowlist that contains the metadata address is refused, "
        "not merely the exact /32",
        _rejects(
            MetadataEndpointExposedError,
            CidrEndpoint,
            description="broad-link-local",
            cidr="169.254.0.0/16",
            ports=(PortRule("TCP", 80),),
        ),
    )
    check(
        "FTF-0902",
        "the Alibaba Cloud metadata address is refused",
        _rejects(
            MetadataEndpointExposedError,
            CidrEndpoint,
            description="alibaba-metadata",
            cidr="100.100.100.200/32",
            ports=(PortRule("TCP", 80),),
        ),
    )
    # Boundary: an RFC1918 address that is numerically similar to a real
    # metadata address (but a different, non-overlapping network entirely)
    # is a genuine, legitimate internal endpoint -- proves the overlap
    # check is precise, not a sloppy substring/prefix match. (A /32
    # literally adjacent to 169.254.169.254, e.g. .253, is no longer a
    # legitimate example under the RFC1918-only restriction below --
    # 169.254.0.0/16 is link-local, not RFC1918 -- see
    # test_cidr_endpoint_requires_rfc1918_private_range.)
    rfc1918_similar_to_alibaba_metadata = CidrEndpoint(
        description="rfc1918-similar-to-alibaba-metadata",
        cidr="10.100.100.200/32",
        ports=(PortRule("TCP", 80),),
    )
    check(
        "FTF-0902",
        "an RFC1918 cidr numerically similar to (but not overlapping) the Alibaba metadata "
        "address is accepted",
        rfc1918_similar_to_alibaba_metadata.cidr == "10.100.100.200/32",
    )
    rfc1918_similar_to_aws_metadata = CidrEndpoint(
        description="rfc1918-similar-to-aws-metadata",
        cidr="10.254.169.254/32",
        ports=(PortRule("TCP", 80),),
    )
    check(
        "FTF-0902",
        "an RFC1918 cidr numerically similar to (but not overlapping) the AWS/GCP/Azure "
        "metadata address is accepted",
        rfc1918_similar_to_aws_metadata.cidr == "10.254.169.254/32",
    )


def test_cidr_endpoint_requires_rfc1918_private_range() -> None:
    # Control-plane decision (2026-10-04T11:00Z review): an explicit
    # internal endpoint must be a real private address, never a public one.
    check(
        "FTF-0902",
        "a public IPv4 cidr (a real public DNS resolver address) is refused",
        _rejects(
            PublicCidrEndpointError,
            CidrEndpoint,
            description="public-dns",
            cidr="8.8.8.8/32",
            ports=(PortRule("TCP", 443),),
        ),
    )
    check(
        "FTF-0902",
        "a link-local address that is not a metadata address is refused as non-RFC1918 "
        "(a distinct failure reason from the metadata-specific rejection above)",
        _rejects(
            PublicCidrEndpointError,
            CidrEndpoint,
            description="link-local-not-metadata",
            cidr="169.254.1.1/32",
            ports=(PortRule("TCP", 80),),
        ),
    )
    rfc1918_boundary_cidrs = (
        "10.0.0.1/32",
        "10.255.255.254/32",
        "172.16.0.1/32",
        "172.31.255.254/32",
        "192.168.0.1/32",
        "192.168.255.254/32",
    )
    for cidr in rfc1918_boundary_cidrs:
        endpoint = CidrEndpoint(description="rfc1918-ok", cidr=cidr, ports=(PortRule("TCP", 80),))
        check("FTF-0902", f"an RFC1918 cidr ({cidr}) is accepted", endpoint.cidr == cidr)


def test_metadata_overlap_is_derived_from_url_policy_cloud_metadata_networks() -> None:
    # DoD (2026-10-04T11:00Z review): "derive metadata from
    # url_policy._CLOUD_METADATA_NETWORKS" -- this test imports that same
    # real list directly (not a hardcoded duplicate) so it automatically
    # tracks any future addition/removal there, rather than silently going
    # stale the way the old 3-probe-address check could.
    ipv4_metadata_cidrs = [str(net) for net in _CLOUD_METADATA_NETWORKS if net.version == 4]
    check(
        "FTF-0902",
        "url_policy defines at least one real IPv4 cloud-metadata network to derive from",
        len(ipv4_metadata_cidrs) >= 1,
    )
    for cidr in ipv4_metadata_cidrs:
        check(
            "FTF-0902",
            f"a cidr exactly matching url_policy's own real metadata network {cidr} is refused",
            _rejects(
                MetadataEndpointExposedError,
                CidrEndpoint,
                description="metadata-derived",
                cidr=cidr,
                ports=(PortRule("TCP", 80),),
            ),
        )


def test_cidr_endpoint_rejects_empty_ports_and_bad_cidr() -> None:
    check(
        "FTF-0902",
        "an endpoint with no explicit ports is refused -- there is no 'all ports' rule",
        _rejects(
            InvalidEndpointDefinitionError,
            CidrEndpoint,
            description="no-ports",
            cidr="10.0.0.5/32",
            ports=(),
        ),
    )
    check(
        "FTF-0902",
        "an unparseable cidr string is refused",
        _rejects(
            InvalidEndpointDefinitionError,
            CidrEndpoint,
            description="garbage",
            cidr="not-a-cidr",
            ports=(PortRule("TCP", 80),),
        ),
    )
    check(
        "FTF-0902",
        "a non-strict cidr (host bits set) is refused rather than silently masked",
        _rejects(
            InvalidEndpointDefinitionError,
            CidrEndpoint,
            description="host-bits-set",
            cidr="10.0.0.5/24",
            ports=(PortRule("TCP", 80),),
        ),
    )


def test_pod_selector_endpoint_requires_explicit_scoping() -> None:
    # negative_acceptance: "rejects one missing explicit endpoint scoping."
    check(
        "FTF-0902",
        "an endpoint with an empty namespace is refused",
        _rejects(
            MissingExplicitScopingError,
            PodSelectorEndpoint,
            description="no-namespace",
            namespace="",
            pod_labels={"app": "x"},
            ports=(PortRule("TCP", 80),),
        ),
    )
    check(
        "FTF-0902",
        "an endpoint with an empty pod_labels selector is refused -- an empty "
        "Kubernetes podSelector matches every pod in the namespace",
        _rejects(
            MissingExplicitScopingError,
            PodSelectorEndpoint,
            description="empty-selector",
            namespace="sovra-shared",
            pod_labels={},
            ports=(PortRule("TCP", 80),),
        ),
    )
    check(
        "FTF-0902",
        "an endpoint with no explicit ports is refused",
        _rejects(
            InvalidEndpointDefinitionError,
            PodSelectorEndpoint,
            description="no-ports",
            namespace="sovra-shared",
            pod_labels={"app": "x"},
            ports=(),
        ),
    )


def test_validate_rejects_default_open_egress_and_ingress() -> None:
    check(
        "FTF-0902",
        "negative_acceptance: deny_by_default_egress=False is refused",
        _rejects(
            NetworkPolicyDefaultOpenError,
            validate_network_policy_template,
            _base_template(deny_by_default_egress=False),
        ),
    )
    check(
        "FTF-0902",
        "deny_by_default_ingress=False is refused (Section 12: CNI enforces "
        "ingress and egress, not egress alone)",
        _rejects(
            NetworkPolicyDefaultOpenError,
            validate_network_policy_template,
            _base_template(deny_by_default_ingress=False),
        ),
    )


def test_validate_rejects_metadata_flag_off() -> None:
    check(
        "FTF-0902",
        "negative_acceptance: block_metadata_endpoint=False is refused",
        _rejects(
            MetadataEndpointExposedError,
            validate_network_policy_template,
            _base_template(block_metadata_endpoint=False),
        ),
    )


def test_validate_rejects_ipv6_policy_bypassing_the_type_checker() -> None:
    # A caller building this dataclass from untrusted deserialized JSON (not
    # through static typing) could pass any string here -- validate must
    # catch it at runtime, not merely rely on the Literal type hint.
    bypassed = _base_template(ipv6_policy="allowed")
    check(
        "FTF-0902",
        "negative_acceptance: ipv6_policy forced to anything but 'blocked' is refused "
        "at runtime, not merely by the type checker",
        _rejects(IPv6EscapeError, validate_network_policy_template, bypassed),
    )


def test_validate_rejects_missing_explicit_endpoint_scoping() -> None:
    check(
        "FTF-0902",
        "negative_acceptance: allowed_internal_endpoints=None ('not yet configured') is refused",
        _rejects(
            MissingExplicitScopingError,
            validate_network_policy_template,
            _base_template(allowed_internal_endpoints=None),
        ),
    )
    check(
        "FTF-0902",
        "an empty pod_selector is refused (matches every pod in the namespace)",
        _rejects(
            MissingExplicitScopingError,
            validate_network_policy_template,
            _base_template(pod_selector={}),
        ),
    )
    check(
        "FTF-0902",
        "an empty template_id is refused",
        _rejects(
            MissingExplicitScopingError,
            validate_network_policy_template,
            _base_template(template_id=""),
        ),
    )
    check(
        "FTF-0902",
        "an empty organization_id is refused",
        _rejects(
            MissingExplicitScopingError,
            validate_network_policy_template,
            _base_template(organization_id=""),
        ),
    )
    check(
        "FTF-0902",
        "an empty namespace is refused",
        _rejects(
            MissingExplicitScopingError,
            validate_network_policy_template,
            _base_template(namespace=""),
        ),
    )


def test_validate_accepts_a_genuinely_empty_endpoint_tuple() -> None:
    # A deliberate, explicit "no internal endpoints needed" workload is a
    # real, different, legitimate case from allowed_internal_endpoints=None.
    closed = _base_template(allowed_internal_endpoints=())
    validated = validate_network_policy_template(closed)
    check(
        "FTF-0902",
        "an explicit empty endpoint tuple (a fully closed workload) validates cleanly",
        validated is closed,
    )


def test_render_manifest_structure_is_deny_all_ingress_allow_listed_egress() -> None:
    template = _base_template()
    manifest = render_network_policy_manifest(template)
    check(
        "FTF-0902",
        "apiVersion is the real networking.k8s.io/v1",
        manifest["apiVersion"] == "networking.k8s.io/v1",
    )
    check("FTF-0902", "kind is NetworkPolicy", manifest["kind"] == "NetworkPolicy")
    check(
        "FTF-0902",
        "metadata.name/namespace match the template",
        manifest["metadata"]["name"] == template.template_id
        and manifest["metadata"]["namespace"] == template.namespace,
    )
    spec = manifest["spec"]
    check(
        "FTF-0902",
        "podSelector matches the template's own pod_selector",
        spec["podSelector"] == {"matchLabels": template.pod_selector},
    )
    check(
        "FTF-0902",
        "policyTypes covers both Ingress and Egress (Section 12: CNI must enforce both)",
        set(spec["policyTypes"]) == {"Ingress", "Egress"},
    )
    check("FTF-0902", "ingress is empty -- deny all inbound traffic", spec["ingress"] == [])
    egress = spec["egress"]
    check(
        "FTF-0902",
        "egress has exactly 3 rules: kube-dns + the 2 configured endpoints",
        len(egress) == 3,
    )
    dns_rule = egress[0]
    check(
        "FTF-0902",
        "the first egress rule is the mandatory kube-dns carve-out",
        dns_rule["to"][0]["namespaceSelector"]["matchLabels"] == KUBE_DNS_NAMESPACE_LABELS
        and dns_rule["to"][0]["podSelector"]["matchLabels"] == KUBE_DNS_POD_LABELS
        and {p["port"] for p in dns_rule["ports"]} == {53},
    )
    cidr_rule = egress[1]
    check(
        "FTF-0902",
        "the CidrEndpoint rule renders a real ipBlock with no host bits/except drift",
        cidr_rule["to"][0]["ipBlock"]["cidr"] == "10.96.0.10/32"
        and cidr_rule["ports"] == [{"protocol": "TCP", "port": 5432}],
    )
    selector_rule = egress[2]
    check(
        "FTF-0902",
        "the PodSelectorEndpoint rule renders a real namespaceSelector+podSelector pair",
        selector_rule["to"][0]["namespaceSelector"]["matchLabels"]
        == {"kubernetes.io/metadata.name": "sovra-shared"}
        and selector_rule["to"][0]["podSelector"]["matchLabels"] == {"app": "mlflow"}
        and selector_rule["ports"] == [{"protocol": "TCP", "port": 5000}],
    )


def test_render_manifest_without_dns_omits_the_dns_rule() -> None:
    template = _base_template(allow_dns=False, allowed_internal_endpoints=())
    manifest = render_network_policy_manifest(template)
    check(
        "FTF-0902",
        "allow_dns=False with no other endpoints renders a fully empty egress list "
        "(genuinely zero egress, not a silently-kept DNS rule)",
        manifest["spec"]["egress"] == [],
    )


def test_render_manifest_always_revalidates_even_a_mutated_template() -> None:
    # A frozen dataclass cannot be reassigned through normal code, but a
    # caller could still build one that bypasses __post_init__ via
    # object.__setattr__, or receive one across a process boundary that
    # skipped validation entirely. render_network_policy_manifest's own
    # docstring claims it "always re-validates first" -- prove that, not
    # merely trust the type hints.
    template = _base_template()
    object.__setattr__(template, "deny_by_default_egress", False)
    check(
        "FTF-0902",
        "render_network_policy_manifest refuses a template mutated to be "
        "default-open after construction, not merely at __post_init__ time",
        _rejects(NetworkPolicyDefaultOpenError, render_network_policy_manifest, template),
    )


def test_render_manifest_revalidates_mutated_endpoint_fields_not_merely_the_template() -> None:
    # Extends the template-level mutation test above to the endpoint level:
    # a frozen CidrEndpoint/PodSelectorEndpoint can also be mutated
    # post-construction via object.__setattr__, and render must catch it
    # fresh every time, not merely trust __post_init__ ran once at
    # construction (2026-10-04T11:00Z review finding 2/3).
    cidr_endpoint = CidrEndpoint(description="x", cidr="10.0.0.5/32", ports=(PortRule("TCP", 80),))
    template = _base_template(allowed_internal_endpoints=(cidr_endpoint,))

    object.__setattr__(cidr_endpoint, "cidr", "0.0.0.0/0")
    check(
        "FTF-0902",
        "a CidrEndpoint mutated to a wildcard cidr after construction is refused at render time",
        _rejects(NetworkPolicyDefaultOpenError, render_network_policy_manifest, template),
    )

    object.__setattr__(cidr_endpoint, "cidr", "169.254.169.254/32")
    check(
        "FTF-0902",
        "a CidrEndpoint mutated to the real metadata address after construction is refused "
        "at render time",
        _rejects(MetadataEndpointExposedError, render_network_policy_manifest, template),
    )

    object.__setattr__(cidr_endpoint, "cidr", "8.8.8.8/32")
    check(
        "FTF-0902",
        "a CidrEndpoint mutated to a public cidr after construction is refused at render time",
        _rejects(PublicCidrEndpointError, render_network_policy_manifest, template),
    )

    object.__setattr__(cidr_endpoint, "cidr", "2001:db8::/32")
    check(
        "FTF-0902",
        "a CidrEndpoint mutated to an IPv6 cidr after construction is refused at render time",
        _rejects(IPv6EscapeError, render_network_policy_manifest, template),
    )

    object.__setattr__(cidr_endpoint, "cidr", "10.0.0.5/32")
    object.__setattr__(cidr_endpoint, "ports", ())
    check(
        "FTF-0902",
        "a CidrEndpoint mutated to an empty ports tuple after construction is refused at "
        "render time",
        _rejects(InvalidEndpointDefinitionError, render_network_policy_manifest, template),
    )

    pod_endpoint = PodSelectorEndpoint(
        description="y",
        namespace="sovra-shared",
        pod_labels={"app": "mlflow"},
        ports=(PortRule("TCP", 5000),),
    )
    pod_template = _base_template(allowed_internal_endpoints=(pod_endpoint,))
    object.__setattr__(pod_endpoint, "pod_labels", {})
    check(
        "FTF-0902",
        "a PodSelectorEndpoint mutated to an empty pod_labels selector after construction is "
        "refused at render time (an empty podSelector would match every pod in the namespace)",
        _rejects(MissingExplicitScopingError, render_network_policy_manifest, pod_template),
    )


def test_render_manifest_rejects_a_subclassed_endpoint_bypassing_post_init() -> None:
    # 2026-10-04T11:00Z review finding 3: "given a mutated, subclassed or
    # duck-typed endpoint, the renderer emits ::/0, metadata, ports: [] and
    # podSelector: {}". A subclass that overrides __post_init__ to skip
    # every invariant still satisfies isinstance(endpoint, CidrEndpoint) --
    # the renderer must reject it by its *exact* type instead.
    class _BypassingCidrEndpoint(CidrEndpoint):
        def __post_init__(self) -> None:  # deliberately skips every real check
            pass

    malicious = _BypassingCidrEndpoint(description="evil", cidr="0.0.0.0/0", ports=())
    check(
        "FTF-0902",
        "constructing the bypassing subclass with malicious fields genuinely skips validation "
        "(otherwise this proves nothing)",
        malicious.cidr == "0.0.0.0/0" and malicious.ports == (),
    )
    template = _base_template(allowed_internal_endpoints=(malicious,))
    check(
        "FTF-0902",
        "a CidrEndpoint subclass overriding __post_init__ to skip validation is refused at "
        "render time by its exact type, not merely accepted via isinstance",
        _rejects(InvalidEndpointDefinitionError, render_network_policy_manifest, template),
    )


def test_render_manifest_rejects_a_duck_typed_endpoint() -> None:
    # 2026-10-04T11:00Z review finding 3, the "duck-typed" half: an object
    # that is not a CidrEndpoint/PodSelectorEndpoint instance at all, but
    # has the same attribute names, must still be refused.
    class _FakeEndpoint:
        description = "fake"
        cidr = "0.0.0.0/0"
        ports = (PortRule("TCP", 80),)

    template = _base_template(allowed_internal_endpoints=(_FakeEndpoint(),))
    check(
        "FTF-0902",
        "a duck-typed object with CidrEndpoint-shaped attributes, but no real CidrEndpoint/"
        "PodSelectorEndpoint type, is refused at render time",
        _rejects(InvalidEndpointDefinitionError, render_network_policy_manifest, template),
    )


def test_yaml_round_trip_matches_the_dict_manifest() -> None:
    template = _base_template()
    manifest = render_network_policy_manifest(template)
    yaml_text = render_network_policy_yaml(template)
    check(
        "FTF-0902",
        "the rendered YAML text parses back to the identical manifest dict",
        yaml.safe_load(yaml_text) == manifest,
    )
    check(
        "FTF-0902",
        "the rendered YAML is real, non-empty NetworkPolicy YAML text",
        "kind: NetworkPolicy" in yaml_text,
    )


def test_manifest_digest_is_deterministic_and_content_sensitive() -> None:
    template = _base_template()
    manifest_a = render_network_policy_manifest(template)
    manifest_b = render_network_policy_manifest(template)
    check(
        "FTF-0902",
        "the same template renders the same manifest digest twice",
        manifest_digest(manifest_a) == manifest_digest(manifest_b),
    )
    other = render_network_policy_manifest(_base_template(template_id="a-materially-different-id"))
    check(
        "FTF-0902",
        "a materially different template renders a different digest",
        manifest_digest(manifest_a) != manifest_digest(other),
    )


_FAULT_INJECTION_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.security.network_policy_templates import (
    CidrEndpoint,
    NetworkPolicyTemplate,
    PortRule,
    validate_network_policy_template,
)

template = NetworkPolicyTemplate(
    schema_version=1,
    template_id="probe",
    organization_id="org-probe",
    project_id=None,
    namespace="sovra-probe",
    pod_selector={{"app": "probe"}},
    cni_profile="calico",
    deny_by_default_egress=False,
    deny_by_default_ingress=True,
    block_metadata_endpoint=True,
    ipv6_policy="blocked",
    allowed_internal_endpoints=(
        CidrEndpoint(description="x", cidr="10.0.0.5/32", ports=(PortRule("TCP", 80),)),
    ),
)
try:
    validate_network_policy_template(template)
    print("MUTATED_MODULE_ACCEPTED_DEFAULT_OPEN")
except Exception as exc:
    print(f"STILL_REJECTED: {{exc}}")
"""


def test_fault_injection_deny_by_default_check_is_load_bearing() -> None:
    """Mutate the real on-disk module to short-circuit the
    deny_by_default_egress check in validate_network_policy_template, then,
    in a fresh subprocess (never importlib.reload), confirm a
    deny_by_default_egress=False template this suite depends on being
    refused genuinely flips to "accepted" once the check is disabled, then
    restore byte-identical (Law 1: prove the removed check was load-bearing,
    not decorative)."""
    original = MODULE_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        pattern = re.compile(r"^(\s*)if (template\.deny_by_default_egress is not True):", re.M)
        check(
            "FTF-0902",
            "expected deny_by_default_egress check found in the real module "
            "(otherwise this proves nothing)",
            pattern.search(src) is not None,
        )
        mutated = pattern.sub(r"\1if False and \2:", src, count=1)
        check("FTF-0902", "fault injection actually changed the file", mutated != src)
        MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = _FAULT_INJECTION_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0902",
            "fault injection: disabling the deny_by_default_egress check is genuinely "
            f"caught by this suite (subprocess stdout={result.stdout!r} "
            f"stderr={result.stderr!r})",
            result.returncode == 0 and "MUTATED_MODULE_ACCEPTED_DEFAULT_OPEN" in result.stdout,
        )
    finally:
        MODULE_PATH.write_bytes(original)
        restored = MODULE_PATH.read_bytes()
        check(
            "FTF-0902",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


_RENDER_TIME_BYPASS_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.security.network_policy_templates import (
    CidrEndpoint,
    NetworkPolicyTemplate,
    PortRule,
    render_network_policy_manifest,
)

endpoint = CidrEndpoint(description="x", cidr="10.0.0.5/32", ports=(PortRule("TCP", 80),))
template = NetworkPolicyTemplate(
    schema_version=1,
    template_id="probe",
    organization_id="org-probe",
    project_id=None,
    namespace="sovra-probe",
    pod_selector={{"app": "probe"}},
    cni_profile="calico",
    deny_by_default_egress=True,
    deny_by_default_ingress=True,
    block_metadata_endpoint=True,
    ipv6_policy="blocked",
    allowed_internal_endpoints=(endpoint,),
)
object.__setattr__(endpoint, "cidr", "0.0.0.0/0")
try:
    render_network_policy_manifest(template)
    print("MUTATED_MODULE_ACCEPTED_WILDCARD_ENDPOINT")
except Exception as exc:
    print(f"STILL_REJECTED: {{exc}}")
"""


def test_fault_injection_render_time_endpoint_revalidation_is_load_bearing() -> None:
    """Additive fault (an inserted `if False:` guard, same pattern as this
    suite's own pre-existing deny_by_default_egress fault test): disables
    the render-time `_validate_cidr_endpoint_fields` call inside
    `_endpoint_egress_rule`, in a fresh subprocess, against an endpoint
    mutated to a wildcard cidr *after* construction (so the already-passed
    __post_init__ check is never the thing being tested here). Proves the
    render-time revalidation call itself is load-bearing, not decorative --
    this is this review's own core finding (2026-10-04T11:00Z, items 2-3)."""
    original = MODULE_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        pattern = re.compile(
            r"cidr, ports = _validate_cidr_endpoint_fields\(\s*"
            r"cidr_endpoint\.description, cidr_endpoint\.cidr, cidr_endpoint\.ports\s*\)"
        )
        check(
            "FTF-0902",
            "expected render-time revalidation call found in the real module "
            "(otherwise this proves nothing)",
            pattern.search(src) is not None,
        )
        mutated = pattern.sub(
            "cidr, ports = cidr_endpoint.cidr, [\n"
            '            {"protocol": r.protocol, "port": r.port} for r in cidr_endpoint.ports\n'
            "        ]",
            src,
            count=1,
        )
        check("FTF-0902", "fault injection actually changed the file", mutated != src)
        MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = _RENDER_TIME_BYPASS_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0902",
            "fault injection: disabling render-time endpoint revalidation lets a "
            "post-construction mutated endpoint through "
            f"(subprocess stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "MUTATED_MODULE_ACCEPTED_WILDCARD_ENDPOINT" in result.stdout,
        )
    finally:
        MODULE_PATH.write_bytes(original)
        restored = MODULE_PATH.read_bytes()
        check(
            "FTF-0902",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


_RFC1918_BYPASS_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.security.network_policy_templates import CidrEndpoint, PortRule

try:
    CidrEndpoint(description="public", cidr="8.8.8.8/32", ports=(PortRule("TCP", 443),))
    print("MUTATED_MODULE_ACCEPTED_PUBLIC_CIDR")
except Exception as exc:
    print(f"STILL_REJECTED: {{exc}}")
"""


def test_fault_injection_rfc1918_check_is_load_bearing_additive() -> None:
    """Additive fault: an inserted `if False and ...` guard (this suite's
    own established pattern) short-circuits the RFC1918-only restriction in
    `_validate_cidr_endpoint_fields`, in a fresh subprocess, then confirms a
    real public cidr (8.8.8.8/32) this suite depends on being refused
    genuinely flips to "accepted" once the check is disabled, then restores
    the module byte-identical."""
    original = MODULE_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        needle = "    if not any(network.subnet_of(rfc1918) for rfc1918 in _RFC1918_NETWORKS):"
        check(
            "FTF-0902",
            "expected RFC1918 check found in the real module (otherwise this proves nothing)",
            needle in src,
        )
        mutated = src.replace(
            needle,
            "    if False and not any(\n"
            "        network.subnet_of(rfc1918) for rfc1918 in _RFC1918_NETWORKS\n"
            "    ):",
            1,
        )
        check("FTF-0902", "fault injection actually changed the file", mutated != src)
        MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = _RFC1918_BYPASS_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0902",
            "fault injection: disabling the RFC1918-only check is genuinely caught by this "
            f"suite (subprocess stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "MUTATED_MODULE_ACCEPTED_PUBLIC_CIDR" in result.stdout,
        )
    finally:
        MODULE_PATH.write_bytes(original)
        restored = MODULE_PATH.read_bytes()
        check(
            "FTF-0902",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


# --- Round 3 (2026-10-06T15:30Z review): render-time exact types, pinned output, ---
# --- PodSelectorEndpoint half, ::/0, RFC1918 boundaries.                        ---


class _TruthyEmptyTuple(tuple):
    def __bool__(self) -> bool:
        return True


class _TruthyEmptyDict(dict):
    def __bool__(self) -> bool:
        return True


class _LyingStr(str):
    """`str(x)` reports a benign value; the real string value is hostile."""

    def __str__(self) -> str:
        return "10.0.0.0/8"


def _render_must_refuse(label: str, template: NetworkPolicyTemplate) -> None:
    try:
        manifest = render_network_policy_manifest(template)
    except NetworkPolicyTemplateError:
        return
    check(
        "FTF-0902",
        f"{label}: render must refuse, but emitted {manifest['spec']['egress']!r}",
        False,
    )


def _cidr_template(**field_overrides: object) -> NetworkPolicyTemplate:
    endpoint = CidrEndpoint(description="x", cidr="10.0.0.5/32", ports=(PortRule("TCP", 80),))
    for name, value in field_overrides.items():
        object.__setattr__(endpoint, name, value)
    return _base_template(allowed_internal_endpoints=(endpoint,))


def _pod_template(**field_overrides: object) -> NetworkPolicyTemplate:
    endpoint = PodSelectorEndpoint(
        description="y",
        namespace="sovra-shared",
        pod_labels={"app": "mlflow"},
        ports=(PortRule("TCP", 5000),),
    )
    for name, value in field_overrides.items():
        object.__setattr__(endpoint, name, value)
    return _base_template(allowed_internal_endpoints=(endpoint,))


def test_r3_render_time_exact_types_refuse_type_lying_endpoint_fields() -> None:
    # Review item 1: every probe in the review's table, on the unmodified code.
    _render_must_refuse(
        "str-subclass cidr lying about __str__ (real 0.0.0.0/0)",
        _cidr_template(cidr=_LyingStr("0.0.0.0/0")),
    )
    _render_must_refuse(
        "str-subclass cidr lying about __str__ (real ::/0)", _cidr_template(cidr=_LyingStr("::/0"))
    )
    _render_must_refuse(
        "str-subclass cidr, benign real value", _cidr_template(cidr=_LyingStr("10.0.0.5/32"))
    )
    _render_must_refuse(
        "truthy-empty tuple-subclass ports", _cidr_template(ports=_TruthyEmptyTuple())
    )
    _render_must_refuse("list ports", _cidr_template(ports=[PortRule("TCP", 80)]))
    _render_must_refuse(
        "tuple-subclass ports with a valid rule",
        _cidr_template(ports=_TruthyEmptyTuple((PortRule("TCP", 80),))),
    )
    port_none = PortRule("TCP", 80)
    object.__setattr__(port_none, "port", None)
    _render_must_refuse("PortRule.port=None", _cidr_template(ports=(port_none,)))
    proto_none = PortRule("TCP", 80)
    object.__setattr__(proto_none, "protocol", None)
    _render_must_refuse("PortRule.protocol=None", _cidr_template(ports=(proto_none,)))
    port_zero = PortRule("TCP", 80)
    object.__setattr__(port_zero, "port", 0)
    _render_must_refuse("PortRule.port=0", _cidr_template(ports=(port_zero,)))
    port_big = PortRule("TCP", 80)
    object.__setattr__(port_big, "port", 65536)
    _render_must_refuse("PortRule.port=65536", _cidr_template(ports=(port_big,)))
    port_bool = PortRule("TCP", 80)
    object.__setattr__(port_bool, "port", True)
    _render_must_refuse("PortRule.port=True", _cidr_template(ports=(port_bool,)))
    proto_sctp = PortRule("TCP", 80)
    object.__setattr__(proto_sctp, "protocol", "SCTP")
    _render_must_refuse("PortRule.protocol=SCTP", _cidr_template(ports=(proto_sctp,)))

    class _PortRuleSub(PortRule):
        def __post_init__(self) -> None:
            pass

    _render_must_refuse("PortRule subclass", _cidr_template(ports=(_PortRuleSub("TCP", 80),)))
    _render_must_refuse("str-subclass description", _cidr_template(description=_LyingStr("d")))

    # Template-level types, same rule.
    _render_must_refuse(
        "empty-dict-subclass template pod_selector", _base_template(pod_selector=_TruthyEmptyDict())
    )
    _render_must_refuse(
        "str-subclass template namespace", _base_template(namespace=_LyingStr("ns"))
    )
    _render_must_refuse(
        "list template allowed_internal_endpoints", _base_template(allowed_internal_endpoints=[])
    )


def test_r3_render_emits_only_normalized_values() -> None:
    template = _cidr_template(cidr="10.0.0.0/8")
    cidr = render_network_policy_manifest(template)["spec"]["egress"][1]["to"][0]["ipBlock"]["cidr"]
    check(
        "FTF-0902",
        "rendered cidr is the normalized str(ip_network)",
        cidr == "10.0.0.0/8" and type(cidr) is str,
    )
    manifest = render_network_policy_manifest(_base_template())
    for rule in manifest["spec"]["egress"]:
        for port in rule["ports"]:
            check("FTF-0902", "rendered port is an exact int", type(port["port"]) is int)
            check("FTF-0902", "rendered protocol is an exact str", type(port["protocol"]) is str)


def test_r3_pod_selector_endpoint_render_time_half() -> None:
    # Review item 3: the PodSelectorEndpoint side was untested.
    class _BypassingPodEndpoint(PodSelectorEndpoint):
        def __post_init__(self) -> None:
            pass

    evil = _BypassingPodEndpoint(description="evil", namespace="", pod_labels={}, ports=())
    _render_must_refuse(
        "PodSelectorEndpoint subclass skipping __post_init__",
        _base_template(allowed_internal_endpoints=(evil,)),
    )
    _render_must_refuse("pod endpoint empty ports", _pod_template(ports=()))
    _render_must_refuse("pod endpoint truthy-empty ports", _pod_template(ports=_TruthyEmptyTuple()))
    _render_must_refuse("pod endpoint list ports", _pod_template(ports=[PortRule("TCP", 5000)]))
    _render_must_refuse(
        "pod endpoint truthy-empty pod_labels", _pod_template(pod_labels=_TruthyEmptyDict())
    )
    _render_must_refuse("pod endpoint empty pod_labels", _pod_template(pod_labels={}))
    _render_must_refuse("pod endpoint blank namespace", _pod_template(namespace=""))
    _render_must_refuse(
        "pod endpoint str-subclass namespace", _pod_template(namespace=_LyingStr("ns"))
    )
    _render_must_refuse("pod endpoint non-str label value", _pod_template(pod_labels={"app": None}))
    _render_must_refuse("pod endpoint non-str label key", _pod_template(pod_labels={1: "x"}))
    _render_must_refuse("pod endpoint empty label value", _pod_template(pod_labels={"app": ""}))
    port_none = PortRule("TCP", 5000)
    object.__setattr__(port_none, "port", None)
    _render_must_refuse("pod endpoint PortRule.port=None", _pod_template(ports=(port_none,)))


def _scan_rendered_spec(spec: dict, declared_cidrs: set[str]) -> list[str]:
    """Structural scan of every peer and port in a rendered spec."""
    problems: list[str] = []
    if spec.get("policyTypes") != ["Ingress", "Egress"]:
        problems.append(f"policyTypes={spec.get('policyTypes')!r}")
    if spec.get("ingress") != []:
        problems.append(f"ingress={spec.get('ingress')!r}")
    if not spec.get("podSelector", {}).get("matchLabels"):
        problems.append("empty template podSelector")
    for rule in spec.get("egress", []):
        if set(rule) != {"to", "ports"}:
            problems.append(f"rule keys {sorted(rule)}")
        if not rule.get("ports"):
            problems.append("rule with no ports")
        for port in rule.get("ports", []):
            if set(port) != {"protocol", "port"}:
                problems.append(f"port keys {sorted(port)} (endPort or extra key)")
            if port.get("protocol") not in ("TCP", "UDP"):
                problems.append(f"protocol {port.get('protocol')!r}")
            number = port.get("port")
            if type(number) is not int or not 1 <= number <= 65535:
                problems.append(f"port {number!r}")
        if not rule.get("to"):
            problems.append("rule with no peers")
        for peer in rule.get("to", []):
            if "ipBlock" in peer:
                if set(peer) != {"ipBlock"} or set(peer["ipBlock"]) != {"cidr"}:
                    problems.append(f"ipBlock peer shape {peer!r}")
                elif peer["ipBlock"]["cidr"] not in declared_cidrs:
                    problems.append(f"undeclared ipBlock {peer['ipBlock']['cidr']!r}")
            else:
                if set(peer) != {"namespaceSelector", "podSelector"}:
                    problems.append(f"selector peer shape {sorted(peer)}")
                for key in ("namespaceSelector", "podSelector"):
                    if not peer.get(key, {}).get("matchLabels"):
                        problems.append(f"empty {key} in {peer!r}")
    return problems


def test_r3_rendered_spec_is_pinned_exactly() -> None:
    # Review item 2: a renderer that adds a peer/port/endPort or drops Egress must go RED.
    dns_rule = {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    expected_base = {
        "podSelector": {"matchLabels": {"app": "training-worker"}},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [],
        "egress": [
            dns_rule,
            {
                "to": [{"ipBlock": {"cidr": "10.96.0.10/32"}}],
                "ports": [{"protocol": "TCP", "port": 5432}],
            },
            {
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "sovra-shared"}
                        },
                        "podSelector": {"matchLabels": {"app": "mlflow"}},
                    }
                ],
                "ports": [{"protocol": "TCP", "port": 5000}],
            },
        ],
    }
    spec = render_network_policy_manifest(_base_template())["spec"]
    check(
        "FTF-0902",
        f"base template spec equals the pinned expected spec (got {spec!r})",
        spec == expected_base,
    )
    check(
        "FTF-0902",
        "base template spec passes the structural scan",
        _scan_rendered_spec(spec, {"10.96.0.10/32"}) == [],
    )

    spec_closed = render_network_policy_manifest(
        _base_template(allowed_internal_endpoints=(), allow_dns=True)
    )["spec"]
    check(
        "FTF-0902",
        "a fully closed workload renders only the DNS rule",
        spec_closed == {**expected_base, "egress": [dns_rule]},
    )
    spec_nodns = render_network_policy_manifest(
        _base_template(allowed_internal_endpoints=(), allow_dns=False)
    )["spec"]
    check(
        "FTF-0902",
        "a closed workload with DNS off renders egress: [] with Egress still in policyTypes",
        spec_nodns == {**expected_base, "egress": []},
    )

    # The scan itself must be able to fail on each review fault shape.
    bad_variants = {
        "::/0 peer on DNS rule": lambda s: s["egress"][0]["to"].append(
            {"ipBlock": {"cidr": "::/0"}}
        ),
        "metadata peer": lambda s: s["egress"][1]["to"].append(
            {"ipBlock": {"cidr": "169.254.169.254/32"}}
        ),
        "empty podSelector peer": lambda s: s["egress"][1]["to"].append({"podSelector": {}}),
        "endPort": lambda s: s["egress"][0]["ports"][0].update({"endPort": 65535}),
        "Egress dropped": lambda s: s.update({"policyTypes": ["Ingress"]}),
    }
    import copy

    for name, mutate in bad_variants.items():
        mutated = copy.deepcopy(spec)
        mutate(mutated)
        check(
            "FTF-0902", f"scan flags: {name}", _scan_rendered_spec(mutated, {"10.96.0.10/32"}) != []
        )
        check("FTF-0902", f"exact pin flags: {name}", mutated != expected_base)


def test_r3_render_normalizes_and_never_aliases_caller_objects() -> None:
    # A bare address is rendered as the normalized /32 -- not echoed raw.
    endpoint = CidrEndpoint(description="x", cidr="10.0.0.5", ports=(PortRule("TCP", 80),))
    rendered = render_network_policy_manifest(
        _base_template(allowed_internal_endpoints=(endpoint,))
    )
    check(
        "FTF-0902",
        "a bare-address cidr is rendered as str(ip_network(...)) == 10.0.0.5/32",
        rendered["spec"]["egress"][1]["to"][0]["ipBlock"]["cidr"] == "10.0.0.5/32",
    )
    # Mutating the caller's objects after rendering must not change the manifest.
    labels = {"app": "mlflow"}
    pod = PodSelectorEndpoint(
        description="y", namespace="sovra-shared", pod_labels=labels, ports=(PortRule("TCP", 5000),)
    )
    selector = {"app": "training-worker"}
    template = _base_template(pod_selector=selector, allowed_internal_endpoints=(pod,))
    manifest = render_network_policy_manifest(template)
    labels["app"] = "evil"
    selector["app"] = "evil"
    check(
        "FTF-0902",
        "the manifest does not alias the endpoint's pod_labels dict",
        manifest["spec"]["egress"][1]["to"][0]["podSelector"]["matchLabels"] == {"app": "mlflow"},
    )
    check(
        "FTF-0902",
        "the manifest does not alias the template's pod_selector dict",
        manifest["spec"]["podSelector"]["matchLabels"] == {"app": "training-worker"},
    )


def test_r3_valid_field_subclass_endpoints_are_refused_by_exact_type() -> None:
    # Field validation alone must not be what refuses these: the fields are valid.
    class _SubCidr(CidrEndpoint):
        def __post_init__(self) -> None:
            pass

    class _SubPod(PodSelectorEndpoint):
        def __post_init__(self) -> None:
            pass

    sub_cidr = _SubCidr(description="x", cidr="10.0.0.5/32", ports=(PortRule("TCP", 80),))
    _render_must_refuse(
        "valid-field CidrEndpoint subclass", _base_template(allowed_internal_endpoints=(sub_cidr,))
    )
    sub_pod = _SubPod(
        description="y",
        namespace="sovra-shared",
        pod_labels={"app": "mlflow"},
        ports=(PortRule("TCP", 5000),),
    )
    _render_must_refuse(
        "valid-field PodSelectorEndpoint subclass",
        _base_template(allowed_internal_endpoints=(sub_pod,)),
    )


def test_r3_ipv6_default_route_is_refused_directly() -> None:
    # Review item 4: `::/0` (and friends) on the real default code.
    for cidr in (
        "::/0",
        "::/1",
        "::",
        "2001:db8::/32",
        "fd00:ec2::254/128",
        "fc00::/7",
        "::ffff:10.0.0.1/128",
    ):
        try:
            CidrEndpoint(description="v6", cidr=cidr, ports=(PortRule("TCP", 80),))
            check("FTF-0902", f"IPv6 cidr {cidr!r} must be refused at construction", False)
        except NetworkPolicyTemplateError:
            pass
        _render_must_refuse(f"IPv6 cidr {cidr!r} mutated in", _cidr_template(cidr=cidr))
    check(
        "FTF-0902",
        "::/0 is the default-open class",
        _rejects(NetworkPolicyDefaultOpenError, CidrEndpoint, "v6", "::/0", (PortRule("TCP", 80),)),
    )
    check(
        "FTF-0902",
        "a non-wildcard IPv6 cidr is the IPv6-escape class",
        _rejects(IPv6EscapeError, CidrEndpoint, "v6", "2001:db8::/32", (PortRule("TCP", 80),)),
    )


def test_r3_rfc1918_boundaries_are_pinned() -> None:
    # Review item 5: ranges must not widen, shrink or gain neighbours.
    refused = (
        "9.255.255.255/32",
        "11.0.0.0/32",
        "10.0.0.0/7",
        "8.0.0.0/7",
        "0.0.0.0/1",
        "172.15.255.255/32",
        "172.32.0.0/32",
        "172.0.0.0/8",
        "172.16.0.0/11",
        "172.16.0.0/10",
        "192.167.255.255/32",
        "192.169.0.0/32",
        "192.168.0.0/15",
        "192.0.0.0/8",
        "100.64.0.1/32",
        "100.64.0.0/10",
        "127.0.0.1/32",
        "1.1.1.1/32",
        "255.255.255.255/32",
    )
    accepted = (
        "10.0.0.0/8",
        "10.0.0.0/32",
        "10.255.255.255/32",
        "172.16.0.0/12",
        "172.16.0.0/32",
        "172.31.255.255/32",
        "192.168.0.0/16",
        "192.168.0.0/32",
        "192.168.255.255/32",
    )
    for cidr in refused:
        try:
            CidrEndpoint(description="b", cidr=cidr, ports=(PortRule("TCP", 80),))
            check("FTF-0902", f"{cidr} must be refused at construction", False)
        except NetworkPolicyTemplateError:
            pass
        _render_must_refuse(f"{cidr} mutated in", _cidr_template(cidr=cidr))
    for cidr in accepted:
        try:
            endpoint = CidrEndpoint(description="b", cidr=cidr, ports=(PortRule("TCP", 80),))
            out = render_network_policy_manifest(
                _base_template(allowed_internal_endpoints=(endpoint,))
            )
            check(
                "FTF-0902",
                f"{cidr} is rendered unchanged",
                out["spec"]["egress"][1]["to"][0]["ipBlock"]["cidr"] == cidr,
            )
        except NetworkPolicyTemplateError as exc:
            check("FTF-0902", f"{cidr} must be accepted, got {exc!r}", False)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0902 Network-policy templates: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0902 Network-policy templates: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
