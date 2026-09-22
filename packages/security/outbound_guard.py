"""FTF-H006 -- shared outbound connection guard (Section 65.4).

This is the *only* module in this repository allowed to open an outbound
HTTP(S) socket on a caller-supplied host. It never trusts a hostname's
string shape: every destination is resolved (or, for an IP literal in any
accepted encoding, normalized) and validated by
`packages.security.url_policy` immediately before the socket connects,
and the connection is pinned to the exact validated address -- a later,
separate DNS lookup can never substitute a different (possibly
disallowed) address for the one this module actually checked
(DNS-rebinding defense). Every followed redirect re-enters this same
validate-then-pin path from scratch; redirects are not followed at all
unless the caller's policy explicitly opts in.

This module never fabricates a response, swallows a connection error into
a fake success, or ignores a certificate the callers's default TLS
context would reject -- see `_PinnedHTTPSConnection`, which always wraps
with the real hostname as `server_hostname` so certificate validation
runs unmodified.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from urllib.parse import urljoin

from packages.security.url_policy import (
    ConnectionPolicy,
    OutboundPolicyError,
    default_port_for_scheme,
    normalize_host_literal,
    validate_resolved_address,
    validate_url_shape,
)

__all__ = [
    "OutboundGuardError",
    "GuardedResponse",
    "resolve_and_validate",
    "guarded_fetch",
]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class OutboundGuardError(Exception):
    """A connection-level failure (DNS/socket/TLS), distinct from a policy
    denial. `OutboundPolicyError` (from `url_policy`) is let propagate
    unwrapped for policy denials, so callers can tell "not authorized"
    apart from "authorized but unreachable" without string matching."""


@dataclass(frozen=True)
class GuardedResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str


def resolve_and_validate(
    hostname: str, port: int, policy: ConnectionPolicy
) -> list[tuple[int, str]]:
    """Resolves `hostname` via real DNS and validates *every* returned
    address against `policy`, failing closed if any one of them is
    disallowed -- a caller cannot be routed to a safe address on one
    attempt and an attacker-controlled address the query happens to
    return on the next, since both would already have to pass here in the
    same resolution. Returns the deduplicated (family, ip_str) pairs in
    the order the resolver returned them.
    """
    try:
        addrinfo = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise OutboundGuardError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    ordered: dict[str, int] = {}
    for family, _socktype, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        if ip_str not in ordered:
            ordered[ip_str] = family

    if not ordered:
        raise OutboundGuardError(f"DNS resolution for {hostname!r} returned no addresses")

    for ip_str in ordered:
        validate_resolved_address(hostname, ipaddress.ip_address(ip_str), policy)

    return [(family, ip_str) for ip_str, family in ordered.items()]


class _PinnedHTTPConnection(HTTPConnection):
    """An HTTPConnection whose `connect()` dials a pre-validated IP address
    directly, rather than re-resolving `self.host` (which stays the real
    hostname, so the Host header this class sends is still correct)."""

    def __init__(self, hostname: str, pinned_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(HTTPSConnection):
    """As `_PinnedHTTPConnection`, but wraps the pinned-address socket with
    TLS using the real hostname as `server_hostname` -- certificate
    validation runs exactly as it would for a normal, non-pinned
    connection to that hostname."""

    def __init__(
        self, hostname: str, pinned_ip: str, port: int, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(hostname, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip
        self._tls_context = context

    def connect(self) -> None:
        raw_sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._tls_context.wrap_socket(raw_sock, server_hostname=self.host)


def _build_connection(
    scheme: str, hostname: str, pinned_ip: str, port: int, timeout: float
) -> HTTPConnection:
    if scheme == "https":
        context = ssl.create_default_context()
        return _PinnedHTTPSConnection(hostname, pinned_ip, port, timeout, context)
    if scheme == "http":
        return _PinnedHTTPConnection(hostname, pinned_ip, port, timeout)
    raise OutboundPolicyError(f"scheme {scheme!r} has no guarded transport")


def guarded_fetch(
    url: str,
    policy: ConnectionPolicy,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
    _redirect_count: int = 0,
) -> GuardedResponse:
    """Validates `url` against `policy`, resolves and validates its real
    destination address, connects pinned to that exact address, and
    issues `method`. A 3xx response is returned as-is unless
    `policy.allow_redirects` -- in which case the redirect target is
    revalidated and redialed from scratch (never trusted from the
    original connection), bounded by `policy.max_redirects`.
    """
    parsed = validate_url_shape(url, policy)
    hostname = parsed.hostname
    if hostname is None:  # pragma: no cover -- validate_url_shape already refused this
        raise OutboundPolicyError("url has no host")
    scheme = parsed.scheme.lower()
    port = parsed.port or default_port_for_scheme(scheme)
    if port is None:  # pragma: no cover -- validate_url_shape already refused this
        raise OutboundPolicyError(f"cannot determine a port for scheme {scheme!r}")

    literal = normalize_host_literal(hostname)
    if literal is not None:
        validate_resolved_address(hostname, literal, policy)
        pinned_ip = str(literal)
    else:
        resolved = resolve_and_validate(hostname, port, policy)
        pinned_ip = resolved[0][1]

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    request_headers = dict(headers or {})
    request_headers.setdefault("Connection", "close")

    conn = _build_connection(scheme, hostname, pinned_ip, port, timeout)
    try:
        conn.request(method, path, body=body, headers=request_headers)
        response: HTTPResponse = conn.getresponse()
        status = response.status
        response_headers = dict(response.getheaders())
        location = response.getheader("Location")
        raw_body = response.read()
    except OSError as exc:
        raise OutboundGuardError(
            f"connection to {hostname!r} ({pinned_ip}:{port}) failed: {exc}"
        ) from exc
    finally:
        conn.close()

    if status in _REDIRECT_STATUSES and location:
        if not policy.allow_redirects:
            return GuardedResponse(
                status=status, headers=response_headers, body=raw_body, final_url=url
            )
        if _redirect_count >= policy.max_redirects:
            raise OutboundPolicyError(
                f"redirect limit ({policy.max_redirects}) exceeded following "
                f"{url!r} -> {location!r}"
            )
        next_url = urljoin(url, location)
        return guarded_fetch(
            next_url,
            policy,
            method=method,
            headers=headers,
            body=body,
            timeout=timeout,
            _redirect_count=_redirect_count + 1,
        )

    return GuardedResponse(status=status, headers=response_headers, body=raw_body, final_url=url)
