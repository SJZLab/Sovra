"""FTF-H006 -- shared outbound URL / destination policy (Section 65.4, 67.1).

This module answers exactly one question: *is a caller allowed to open a
network connection to this scheme/host/port/resolved-address combination?*
It never dials a socket itself -- that is `packages.security.outbound_guard`,
which uses these functions to validate a destination before connecting and
again after every DNS resolution and every followed redirect (Section 65.4:
"Revalidate after resolution and every allowed redirect; default no
redirects.").

Every future caller (LiteLLM connections, dataset source connectors, repo
URLs, webhooks, model/artifact fetchers -- FTF-0203/FTF-0302/FTF-0303/
FTF-04A01/FTF-0500B/FTF-0500C) shares this one guard rather than each
re-deriving its own ad hoc SSRF check, per Section 65.4's own "one shared
outbound-connection guard" requirement.

Cloud-metadata destinations are always denied, even if an operator
explicitly allowlists a private range that happens to contain them --
Section 65.4's own negative_acceptance: "a blanket private-address
rejection cannot break intended internal LiteLLM/storage while metadata
remains denied" is read literally: an explicit allowlist authorizes real
internal services, never the metadata identity itself.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

__all__ = [
    "OutboundPolicyError",
    "ConnectionPolicy",
    "IPNetwork",
    "normalize_host_literal",
    "validate_url_shape",
    "validate_resolved_address",
    "is_cloud_metadata_address",
    "default_port_for_scheme",
]


class OutboundPolicyError(Exception):
    """A destination or URL shape is denied by an outbound connection policy."""


IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

DEFAULT_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})

# Section 65.4: "Do not support arbitrary file:, gopher:, FTP or userinfo
# URLs." -- there is no allowlist path that can re-enable these; they are
# refused at shape validation regardless of policy.
_FORBIDDEN_SCHEMES: frozenset[str] = frozenset({"file", "gopher", "ftp"})

# Cloud-metadata identities -- link-local addresses that specifically serve
# instance credentials/metadata on every major cloud provider. Denied
# unconditionally: never present in any policy's explicit_private_allowlist,
# and checked *before* the general private/loopback/link-local rule so an
# operator cannot accidentally (or deliberately) re-open it via a broad
# link-local allowlist entry.
_CLOUD_METADATA_NETWORKS: tuple[IPNetwork, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),  # AWS / GCP / Azure / DigitalOcean IMDS
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDSv2 (IPv6)
)
_CLOUD_METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "metadata",
    }
)


def is_cloud_metadata_address(ip: IPAddress) -> bool:
    return any(ip in network for network in _CLOUD_METADATA_NETWORKS)


@dataclass(frozen=True)
class ConnectionPolicy:
    """One named outbound-connection policy.

    `explicit_private_allowlist` is the mechanism Section 65.4 requires for
    "internal org endpoints are separately allowlisted, not blanket-blocked"
    -- a caller that genuinely needs to reach an internal LiteLLM/storage
    endpoint on a private range lists that exact network here. It never
    authorizes a cloud-metadata address (see `is_cloud_metadata_address`,
    checked independently of this list).
    """

    name: str
    allowed_schemes: frozenset[str] = DEFAULT_ALLOWED_SCHEMES
    allowed_ports: frozenset[int] | None = None
    explicit_private_allowlist: tuple[IPNetwork, ...] = field(default_factory=tuple)
    allow_redirects: bool = False
    max_redirects: int = 0


_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}


def default_port_for_scheme(scheme: str) -> int | None:
    return _DEFAULT_PORT_BY_SCHEME.get(scheme.lower())


def _ports_allowed_for(policy: ConnectionPolicy, scheme: str) -> frozenset[int]:
    if policy.allowed_ports is not None:
        return policy.allowed_ports
    default_port = _DEFAULT_PORT_BY_SCHEME.get(scheme)
    return frozenset({default_port}) if default_port is not None else frozenset()


def validate_url_shape(url: str, policy: ConnectionPolicy) -> SplitResult:
    """Structural validation only -- scheme, userinfo, host, port. Does not
    resolve DNS and does not know whether the destination is reachable or
    authorized; see `validate_resolved_address` for that.
    """
    if not isinstance(url, str) or not url:
        raise OutboundPolicyError("url must be a non-empty string")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise OutboundPolicyError(f"url is not parseable: {url!r}") from exc

    scheme = parsed.scheme.lower()
    if scheme in _FORBIDDEN_SCHEMES:
        raise OutboundPolicyError(f"scheme {scheme!r} is never permitted")
    if scheme not in policy.allowed_schemes:
        raise OutboundPolicyError(
            f"scheme {scheme!r} is not in policy {policy.name!r}'s allowed_schemes"
        )
    if parsed.username is not None or parsed.password is not None:
        raise OutboundPolicyError("userinfo (user:pass@) in a URL is never permitted")
    if not parsed.hostname:
        raise OutboundPolicyError("url has no host")

    try:
        port = parsed.port
    except ValueError as exc:
        raise OutboundPolicyError(f"url has an invalid port: {url!r}") from exc
    resolved_port = port if port is not None else _DEFAULT_PORT_BY_SCHEME.get(scheme)
    if resolved_port is None:
        raise OutboundPolicyError(f"cannot determine a port for scheme {scheme!r}")
    allowed_ports = _ports_allowed_for(policy, scheme)
    if allowed_ports and resolved_port not in allowed_ports:
        raise OutboundPolicyError(
            f"port {resolved_port} is not in policy {policy.name!r}'s allowed_ports"
        )
    return parsed


def validate_resolved_address(
    hostname: str,
    ip: IPAddress,
    policy: ConnectionPolicy,
) -> None:
    """Raises OutboundPolicyError unless `ip` is a permitted destination for
    this policy. Called once per resolved address -- `outbound_guard`
    requires every address a hostname resolves to to pass before it will
    use any of them, so a multi-answer DNS response cannot smuggle one
    disallowed address past validation of the others.
    """
    if is_cloud_metadata_address(ip) or hostname.lower() in _CLOUD_METADATA_HOSTNAMES:
        raise OutboundPolicyError(
            f"{ip} ({hostname!r}) is a cloud-metadata destination -- never permitted, "
            "regardless of any explicit allowlist entry"
        )

    is_non_public = (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
    if not is_non_public:
        return

    if any(ip in network for network in policy.explicit_private_allowlist):
        return

    raise OutboundPolicyError(
        f"{ip} ({hostname!r}) is a non-public destination not present in policy "
        f"{policy.name!r}'s explicit_private_allowlist"
    )


# -- Alternate-encoding IP literal normalization -----------------------------
#
# Section 65.4: "Defend IPv6/mapped-IP/encoded-address and DNS-rebinding
# cases." A URL host component can spell the same IPv4 address many ways
# that `ipaddress.ip_address` alone will not accept: a bare 32-bit integer
# ("2130706433"), an octal or hex per-octet dotted form ("0177.0.0.1",
# "0x7f.0x0.0x0.0x1"), or the legacy inet_aton "fewer than four parts"
# collapsing form ("127.1" == "127.0.0.1"). Any of these used as a URL
# hostname bypasses a naive string-equality loopback/private check while
# still routing to the real address -- this function is the single place
# that recognizes all of them so `validate_resolved_address` always sees a
# real `ipaddress` object, never a string an attacker chose the shape of.

_DOTTED_PART = re.compile(r"^(?:0[xX][0-9a-fA-F]+|0[0-7]+|0|[1-9][0-9]*)$")


def _parse_ipv4_part(part: str) -> int | None:
    if not _DOTTED_PART.match(part):
        return None
    try:
        if part.lower().startswith("0x"):
            return int(part, 16)
        if len(part) > 1 and part[0] == "0":
            return int(part, 8)
        return int(part, 10)
    except ValueError:
        return None


def _parse_legacy_ipv4_literal(host: str) -> ipaddress.IPv4Address | None:
    """Accepts inet_aton-style forms: 1-4 dotted parts (decimal, octal with
    a leading 0, or 0x-prefixed hex), where all but the last part must fit
    in a byte and the last part absorbs the remaining bits."""
    if not host or host.count(".") > 3:
        return None
    parts = host.split(".")
    values: list[int] = []
    for part in parts:
        value = _parse_ipv4_part(part)
        if value is None:
            return None
        values.append(value)

    if len(values) == 1:
        total = values[0]
    elif len(values) == 2:
        if values[0] > 0xFF or values[1] > 0xFFFFFF:
            return None
        total = (values[0] << 24) | values[1]
    elif len(values) == 3:
        if values[0] > 0xFF or values[1] > 0xFF or values[2] > 0xFFFF:
            return None
        total = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        if any(v > 0xFF for v in values):
            return None
        total = (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]

    if total > 0xFFFFFFFF:
        return None
    return ipaddress.IPv4Address(total)


def normalize_host_literal(host: str) -> IPAddress | None:
    """Returns the real `ipaddress` object `host` denotes if it is an IP
    literal in *any* accepted encoding (standard dotted-quad, standard
    IPv6, bracketed IPv6, IPv4-mapped IPv6, bare integer, octal/hex dotted,
    or a collapsed inet_aton form). Returns None if `host` is not an IP
    literal at all (an ordinary DNS name), in which case the caller must
    resolve it via DNS and validate every resolved address instead.
    """
    if not host:
        return None
    candidate = host[1:-1] if host.startswith("[") and host.endswith("]") else host

    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass

    return _parse_legacy_ipv4_literal(candidate)
