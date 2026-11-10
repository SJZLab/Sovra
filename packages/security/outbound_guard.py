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
import math
import socket
import ssl
import threading
import time
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
    "GuardedStreamResponse",
    "resolve_and_validate",
    "guarded_fetch",
    "guarded_stream_fetch",
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
        self._deadline_expired = threading.Event()

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        _refuse_if_deadline_expired(self)


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
        self._deadline_expired = threading.Event()

    def connect(self) -> None:
        raw_sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        # `wrap_socket` detaches `raw_sock`, so a watchdog that fires while the
        # handshake runs has nothing to shut down; the expiry flag is checked
        # once the handshake returns instead.
        self.sock = self._tls_context.wrap_socket(raw_sock, server_hostname=self.host)
        _refuse_if_deadline_expired(self)


def _refuse_if_deadline_expired(conn: HTTPConnection) -> None:
    """Called right after a connection's socket is assigned. The watchdog sets
    `_deadline_expired` BEFORE it aborts, so either its abort saw the socket
    or this check sees the flag -- the status line and headers are never read
    on a connection whose total deadline has already passed."""
    event = getattr(conn, "_deadline_expired", None)
    if event is not None and event.is_set():
        _abort_connection(conn)
        raise OSError("call deadline exceeded before the connection was established")


def _abort_connection(conn: HTTPConnection) -> None:
    """Unblocks a thread that is reading from `conn` right now: `shutdown`
    wakes a blocked `recv` (a bare `close` from another thread does not),
    then the descriptors are released. Never raises."""
    sock = getattr(conn, "sock", None)
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _build_connection(
    scheme: str, hostname: str, pinned_ip: str, port: int, timeout: float
) -> HTTPConnection:
    if scheme == "https":
        context = ssl.create_default_context()
        return _PinnedHTTPSConnection(hostname, pinned_ip, port, timeout, context)
    if scheme == "http":
        return _PinnedHTTPConnection(hostname, pinned_ip, port, timeout)
    raise OutboundPolicyError(f"scheme {scheme!r} has no guarded transport")


def _prepare_pinned_connection(
    url: str, policy: ConnectionPolicy, timeout: float
) -> tuple[HTTPConnection, str, str]:
    """The validate-then-pin path shared by `guarded_fetch` and
    `guarded_stream_fetch`: structural validation, DNS resolution (or IP
    literal normalization) with every candidate address checked against
    `policy`, then a connection dialed to the exact one address that was
    actually validated (never a later, separate lookup). Returns the open,
    not-yet-requested connection plus the request path and hostname.
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

    conn = _build_connection(scheme, hostname, pinned_ip, port, timeout)
    return conn, path, hostname


@dataclass
class GuardedStreamResponse:
    """An open, guarded connection whose response body has *not* been read
    yet -- for a real server-sent-events stream, which must never be
    buffered whole into memory the way `GuardedResponse.body` is. Status
    and headers are already available (the response line and header block
    have been read); the body is read incrementally via `read_chunk`.

    Always used as a context manager (or with an explicit `close()` in a
    `finally`): `close()` is the only thing that actually releases the
    real underlying socket, and it is idempotent so a caller cancelling
    mid-stream can call it without first checking whether the stream
    already ended on its own.
    """

    status: int
    headers: dict[str, str]
    final_url: str
    _connection: HTTPConnection
    _response: HTTPResponse
    _closed: bool = False

    def read_chunk(self, size: int = 4096) -> bytes:
        """Reads up to `size` bytes of the real response body, returning
        as soon as any data is available rather than waiting to fill
        `size` -- genuinely incremental, for a real server-sent-events
        stream where a caller must see each delta as it arrives, not once
        enough bytes have accumulated. Uses `read1`, not `read`: the
        latter is `io.BufferedReader`'s ordinary "read exactly `size`
        bytes, issuing as many underlying socket reads as it takes, or
        until EOF" behavior, which would silently defeat streaming for any
        response smaller than `size` by blocking until the connection
        closes. Returns `b""` at genuine stream end (or if already
        closed) -- never blocks past the connection's own configured
        timeout."""
        if self._closed:
            return b""
        return self._response.read1(size)

    def close(self) -> None:
        """Closes the real underlying connection now, regardless of
        whether the body was fully read -- this is what makes caller
        cancellation release the socket immediately rather than leaking
        it until the server eventually times out. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._connection.close()

    def __enter__(self) -> GuardedStreamResponse:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


def guarded_stream_fetch(
    url: str,
    policy: ConnectionPolicy,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
    deadline: float | None = None,
) -> GuardedStreamResponse:
    """As `guarded_fetch`, but returns the response open for incremental
    reads instead of buffering the whole body -- for a real chunked
    server-sent-events stream. Uses the identical validate-then-pin
    connection path (`_prepare_pinned_connection`); the only difference is
    that the body is left unread here.

    Redirects are never followed here (unlike `guarded_fetch`): by the
    time a 3xx could be distinguished from a 200 for a streamed body, a
    caller may already be reading it, so silently reconnecting elsewhere
    would discard real, possibly non-empty state. No caller of this
    function targets a redirectable endpoint.

    `timeout` bounds each individual socket operation, so a server that
    drips one byte per interval can hold the connect / status-line /
    header phase open indefinitely. `deadline` (seconds, measured on the
    monotonic clock from entry; default `None` = no total bound, the
    behaviour every existing caller gets unchanged) is one TOTAL budget for
    connect + TLS + status line + headers: when it expires the connection
    is shut down and `OutboundGuardError` is raised. It does not bound the
    body, which the caller reads (and bounds) itself via `read_chunk`.
    """
    if deadline is not None and (
        isinstance(deadline, bool)
        or not isinstance(deadline, int | float)
        or not math.isfinite(deadline)
        or deadline <= 0
    ):
        raise OutboundGuardError("deadline must be a positive finite number of seconds")
    started = time.monotonic()
    conn, path, hostname = _prepare_pinned_connection(url, policy, timeout)
    request_headers = dict(headers or {})
    request_headers.setdefault("Connection", "close")

    watchdog: threading.Timer | None = None
    lock = threading.Lock()
    state = {"done": False, "expired": False}
    if deadline is not None:
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            conn.close()
            raise OutboundGuardError(f"connection to {hostname!r} exceeded the call deadline")
        conn.timeout = min(timeout, remaining)

        def _expire() -> None:
            with lock:
                if state["done"]:
                    return
                state["expired"] = True
            event = getattr(conn, "_deadline_expired", None)
            if event is not None:
                event.set()
            _abort_connection(conn)

        watchdog = threading.Timer(remaining, _expire)
        watchdog.daemon = True
        watchdog.start()

    def _finish() -> bool:
        """Stops the watchdog; True when it had already fired."""
        with lock:
            state["done"] = True
            fired = state["expired"]
        if watchdog is not None:
            watchdog.cancel()
        return fired

    try:
        conn.request(method, path, body=body, headers=request_headers)
        response: HTTPResponse = conn.getresponse()
    except BaseException as exc:
        fired = _finish()
        if fired:
            conn.close()
            raise OutboundGuardError(
                f"connection to {hostname!r} exceeded the call deadline"
            ) from None
        if isinstance(exc, OSError):
            conn.close()
            raise OutboundGuardError(f"connection to {hostname!r} failed: {exc}") from exc
        raise
    if _finish():
        conn.close()
        raise OutboundGuardError(f"connection to {hostname!r} exceeded the call deadline")

    return GuardedStreamResponse(
        status=response.status,
        headers=dict(response.getheaders()),
        final_url=url,
        _connection=conn,
        _response=response,
    )


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
    conn, path, hostname = _prepare_pinned_connection(url, policy, timeout)
    request_headers = dict(headers or {})
    request_headers.setdefault("Connection", "close")

    try:
        conn.request(method, path, body=body, headers=request_headers)
        response: HTTPResponse = conn.getresponse()
        status = response.status
        response_headers = dict(response.getheaders())
        location = response.getheader("Location")
        raw_body = response.read()
    except OSError as exc:
        raise OutboundGuardError(f"connection to {hostname!r} failed: {exc}") from exc
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
