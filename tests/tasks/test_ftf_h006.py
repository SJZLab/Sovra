#!/usr/bin/env python3
"""FTF-H006 -- Shared outbound URL and credential guard.

Runnable without any database or external network egress -- every test
below either calls `packages.security.url_policy`'s pure functions
directly, or drives `packages.security.outbound_guard.guarded_fetch`
against a real HTTP server this suite starts on `127.0.0.1` itself (a
real local socket, never a mocked response object), matching the
no-DB/no-external-egress local-server pattern already established by
tests/tasks/test_ftf_0103.py and tests/tasks/test_ftf_h003.py.

Section 65.4's acceptance clause ("exercise real controlled HTTP
endpoints and DNS/redirect fixtures covering loopback, private
allowlist, link-local metadata, IPv6, alternate encodings and
rebinding") is covered as follows:

  - loopback / private allowlist: real fetches against the local server,
    denied by default and allowed once explicitly allowlisted;
  - link-local metadata: pure `validate_resolved_address` checks (this
    sandbox has no route to a real metadata service, and must not
    attempt one -- the denial is proven at the policy layer, which is
    exactly what a real connection attempt would consult first);
  - IPv6: loopback (`::1`) and an IPv4-mapped IPv6 literal, both pure and
    (for `::1`) a real fetch once explicitly allowlisted;
  - alternate encodings: a real fetch that reaches the same local server
    via a decimal-integer and an octal-dotted spelling of 127.0.0.1;
  - rebinding: a real fetch where DNS resolution is (necessarily, since
    this sandbox controls no real DNS zone) mocked, but the actual
    socket connection is real -- proving resolution happens exactly once
    and the live connection is pinned to that one validated address,
    never a second, potentially different, answer.

Run directly: python3 tests/tasks/test_ftf_h006.py
"""

from __future__ import annotations

import atexit
import ipaddress
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.security.outbound_guard import (  # noqa: E402
    GuardedResponse,
    guarded_fetch,
)
from packages.security.url_policy import (  # noqa: E402
    ConnectionPolicy,
    OutboundPolicyError,
    normalize_host_literal,
    validate_resolved_address,
    validate_url_shape,
)

URL_POLICY_PATH = ROOT / "packages" / "security" / "url_policy.py"

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local HTTP fixture -------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _respond(self, status: int, body: bytes = b"", location: str | None = None) -> None:
        self.send_response(status)
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ok":
            self._respond(200, b"guarded-ok")
        elif self.path == "/redirect-to-ok":
            self._respond(302, location="/ok")
        elif self.path == "/redirect-loop":
            self._respond(302, location="/redirect-loop")
        else:
            self._respond(404)


_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
_SERVER_PORT = _SERVER.server_address[1]
_SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True)
_SERVER_THREAD.start()
atexit.register(_SERVER.shutdown)


def _detect_ipv6_support() -> bool:
    """This container/sandbox may have no IPv6 stack at all (kernel-level,
    confirmed via a real bind attempt, not merely a Python capability
    flag) -- disclosed explicitly below rather than silently skipped, same
    resource_gate rationale already established for FTF-0004/FTF-0102/
    FTF-0104/FTF-1601/FTF-H003's own no-container-registry-egress notes.
    """
    if not socket.has_ipv6:
        return False
    try:
        probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            probe.bind(("::1", 0))
        finally:
            probe.close()
        return True
    except OSError:
        return False


_IPV6_SUPPORTED = _detect_ipv6_support()
_SERVER_V6: ThreadingHTTPServer | None = None
_SERVER_V6_PORT: int | None = None
if _IPV6_SUPPORTED:
    _SERVER_V6 = ThreadingHTTPServer(("::1", 0), _Handler)
    _SERVER_V6_PORT = _SERVER_V6.server_address[1]
    _server_v6_thread = threading.Thread(target=_SERVER_V6.serve_forever, daemon=True)
    _server_v6_thread.start()
    atexit.register(_SERVER_V6.shutdown)
else:
    print(
        "FTF-H006: this sandbox has no IPv6 stack at all (real AF_INET6 bind to "
        "::1 fails at the kernel level) -- the live ::1 guarded_fetch sub-check "
        "is skipped and disclosed here; the pure IPv6 policy-validation checks "
        "(ipaddress-object-level, no socket required) still run and still count."
    )

LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
LOOPBACK_V6 = ipaddress.ip_network("::1/128")

_ALL_TEST_PORTS = frozenset({_SERVER_PORT} | ({_SERVER_V6_PORT} if _SERVER_V6_PORT else set()))

_OPEN_HTTP = ConnectionPolicy(
    name="test-open-http",
    allowed_schemes=frozenset({"http"}),
    allowed_ports=frozenset({_SERVER_PORT}),
)
_ALLOWLISTED_HTTP = ConnectionPolicy(
    name="test-allowlisted-http",
    allowed_schemes=frozenset({"http"}),
    allowed_ports=_ALL_TEST_PORTS,
    explicit_private_allowlist=(LOOPBACK_V4, LOOPBACK_V6),
)
_REDIRECTING_HTTP = ConnectionPolicy(
    name="test-redirecting-http",
    allowed_schemes=frozenset({"http"}),
    allowed_ports=_ALL_TEST_PORTS,
    explicit_private_allowlist=(LOOPBACK_V4, LOOPBACK_V6),
    allow_redirects=True,
    max_redirects=3,
)


def _url(path: str, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{_SERVER_PORT}{path}"


def _url_v6(path: str) -> str:
    assert _SERVER_V6_PORT is not None
    return f"http://[::1]:{_SERVER_V6_PORT}{path}"


# -- 1. Shape validation ------------------------------------------------------


def test_forbidden_schemes_are_always_refused() -> None:
    policy = ConnectionPolicy(name="p", allowed_schemes=frozenset({"http", "https", "file"}))
    for scheme in ("file", "gopher", "ftp"):
        try:
            validate_url_shape(f"{scheme}://host/path", policy)
            check("FTF-H006", f"scheme {scheme!r} is refused even if policy allows it", False)
        except OutboundPolicyError:
            check("FTF-H006", f"scheme {scheme!r} is refused even if policy allows it", True)


def test_userinfo_in_url_is_refused() -> None:
    try:
        validate_url_shape("https://user:pass@host/path", ConnectionPolicy(name="p"))
        check("FTF-H006", "userinfo (user:pass@) in a URL is refused", False)
    except OutboundPolicyError:
        check("FTF-H006", "userinfo (user:pass@) in a URL is refused", True)


def test_scheme_not_in_policy_allowlist_is_refused() -> None:
    policy = ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"}))
    try:
        validate_url_shape("http://host/path", policy)
        check("FTF-H006", "a scheme not in the policy's allowed_schemes is refused", False)
    except OutboundPolicyError:
        check("FTF-H006", "a scheme not in the policy's allowed_schemes is refused", True)


def test_port_outside_policy_allowlist_is_refused() -> None:
    policy = ConnectionPolicy(name="p", allowed_ports=frozenset({443}))
    try:
        validate_url_shape("https://host:8443/path", policy)
        check("FTF-H006", "a port outside the policy's allowed_ports is refused", False)
    except OutboundPolicyError:
        check("FTF-H006", "a port outside the policy's allowed_ports is refused", True)


# -- 2. loopback / private allowlist -----------------------------------------


def test_loopback_denied_by_default() -> None:
    try:
        guarded_fetch(_url("/ok"), _OPEN_HTTP)
        check("FTF-H006", "acceptance: loopback destination denied with no allowlist entry", False)
    except OutboundPolicyError:
        check("FTF-H006", "acceptance: loopback destination denied with no allowlist entry", True)


def test_loopback_reachable_once_explicitly_allowlisted() -> None:
    response = guarded_fetch(_url("/ok"), _ALLOWLISTED_HTTP)
    check(
        "FTF-H006",
        "negative_acceptance: an explicitly authorized private destination is "
        "genuinely reachable, real body returned by the real local server",
        isinstance(response, GuardedResponse)
        and response.status == 200
        and response.body == b"guarded-ok",
    )


# -- 3. link-local metadata: always denied, even under a broad allowlist ----


def test_cloud_metadata_v4_denied_even_with_broad_private_allowlist() -> None:
    broad_policy = ConnectionPolicy(
        name="broad", explicit_private_allowlist=(ipaddress.ip_network("169.254.0.0/16"),)
    )
    try:
        validate_resolved_address(
            "instance-metadata", ipaddress.ip_address("169.254.169.254"), broad_policy
        )
        check(
            "FTF-H006",
            "negative_acceptance: cloud metadata (169.254.169.254) stays denied "
            "even when the containing /16 is explicitly allowlisted",
            False,
        )
    except OutboundPolicyError:
        check(
            "FTF-H006",
            "negative_acceptance: cloud metadata (169.254.169.254) stays denied "
            "even when the containing /16 is explicitly allowlisted",
            True,
        )


def test_cloud_metadata_v6_denied() -> None:
    try:
        validate_resolved_address(
            "instance-metadata-v6",
            ipaddress.ip_address("fd00:ec2::254"),
            ConnectionPolicy(name="p"),
        )
        check("FTF-H006", "AWS IPv6 metadata address (fd00:ec2::254) is denied", False)
    except OutboundPolicyError:
        check("FTF-H006", "AWS IPv6 metadata address (fd00:ec2::254) is denied", True)


def test_generic_link_local_denied_by_default_but_allowlistable() -> None:
    ip = ipaddress.ip_address("169.254.1.1")
    try:
        validate_resolved_address("link-local-host", ip, ConnectionPolicy(name="p"))
        check("FTF-H006", "a non-metadata link-local address is denied by default", False)
    except OutboundPolicyError:
        check("FTF-H006", "a non-metadata link-local address is denied by default", True)

    allowed_policy = ConnectionPolicy(
        name="p", explicit_private_allowlist=(ipaddress.ip_network("169.254.1.0/24"),)
    )
    try:
        validate_resolved_address("link-local-host", ip, allowed_policy)
        allowed = True
    except OutboundPolicyError:
        allowed = False
    check(
        "FTF-H006",
        "a non-metadata link-local address can be explicitly authorized "
        "(distinct from the metadata identity itself)",
        allowed,
    )


# -- 4. IPv6 -------------------------------------------------------------


def test_ipv6_loopback_denied_by_default_and_reachable_once_allowlisted() -> None:
    try:
        validate_resolved_address(
            "v6-loopback", ipaddress.ip_address("::1"), ConnectionPolicy(name="p")
        )
        check("FTF-H006", "::1 is denied by default", False)
    except OutboundPolicyError:
        check("FTF-H006", "::1 is denied by default", True)

    if not _IPV6_SUPPORTED:
        return  # disclosed at module load time -- this sandbox has no IPv6 stack at all

    response = guarded_fetch(_url_v6("/ok"), _ALLOWLISTED_HTTP)
    check(
        "FTF-H006",
        "acceptance (IPv6): ::1, once explicitly allowlisted, is a real reachable "
        "destination via the same guarded_fetch path",
        response.status == 200 and response.body == b"guarded-ok",
    )


def test_ipv4_mapped_ipv6_literal_normalizes_to_the_real_v4_loopback() -> None:
    literal = normalize_host_literal("::ffff:127.0.0.1")
    check(
        "FTF-H006",
        "an IPv4-mapped IPv6 literal normalizes to a real ipaddress object",
        literal is not None and literal.is_loopback,
    )


# -- 5. alternate encodings ----------------------------------------------


def test_alternate_ipv4_encodings_normalize_to_loopback() -> None:
    for literal_host in ("2130706433", "0x7f000001", "017700000001", "0177.0.0.1", "127.1"):
        parsed = normalize_host_literal(literal_host)
        check(
            "FTF-H006",
            f"alternate encoding {literal_host!r} normalizes to a real loopback address",
            parsed is not None and parsed == ipaddress.ip_address("127.0.0.1"),
        )


def test_alternate_encoding_host_reaches_the_real_local_server_once_allowlisted() -> None:
    for literal_host in ("2130706433", "0x7f000001", "0177.0.0.1"):
        response = guarded_fetch(_url("/ok", host=literal_host), _ALLOWLISTED_HTTP)
        check(
            "FTF-H006",
            f"acceptance: alternate-encoded loopback host {literal_host!r} genuinely "
            "dials the same real local server as '127.0.0.1', not a bypass",
            response.status == 200 and response.body == b"guarded-ok",
        )


def test_ordinary_dns_name_is_not_treated_as_an_ip_literal() -> None:
    check(
        "FTF-H006",
        "an ordinary hostname is left for DNS resolution, not misparsed as a literal",
        normalize_host_literal("api.example.com") is None,
    )


def test_garbage_host_is_not_misparsed_as_an_ip_literal() -> None:
    for bad in ("999.999.999.999", "1.2.3.4.5", "not-an-ip", ""):
        check(
            "FTF-H006",
            f"garbage host {bad!r} is not misparsed into a real address",
            normalize_host_literal(bad) is None,
        )


# -- 6. redirects: default denied, explicit opt-in revalidates --------------


def test_redirect_not_followed_by_default() -> None:
    response = guarded_fetch(_url("/redirect-to-ok"), _ALLOWLISTED_HTTP)
    check(
        "FTF-H006",
        "negative_acceptance: default policy does not follow a redirect -- the "
        "3xx itself is returned to the caller",
        response.status == 302,
    )


def test_redirect_followed_and_revalidated_when_opted_in() -> None:
    response = guarded_fetch(_url("/redirect-to-ok"), _REDIRECTING_HTTP)
    check(
        "FTF-H006",
        "acceptance: with allow_redirects, the redirect target is really "
        "re-dialed and its real response returned",
        response.status == 200 and response.body == b"guarded-ok",
    )


def test_redirect_loop_bounded_by_max_redirects() -> None:
    try:
        guarded_fetch(_url("/redirect-loop"), _REDIRECTING_HTTP)
        check("FTF-H006", "a redirect loop is bounded by policy.max_redirects, not infinite", False)
    except OutboundPolicyError:
        check("FTF-H006", "a redirect loop is bounded by policy.max_redirects, not infinite", True)


# -- 7. DNS-rebinding defense: resolve once, connect pinned ------------------


def test_dns_resolved_once_and_connection_pinned_to_that_address() -> None:
    """A hostile resolver that answers differently on a second lookup must
    never affect a single guarded_fetch call: this test proves
    `socket.getaddrinfo` is consulted exactly once and the real socket
    connects to exactly that first, already-validated address -- even
    though a second (uninspected) call is rigged to return a disallowed
    address, proving the guard never makes that second call at all."""
    safe_ip = "127.0.0.1"
    unsafe_ip = "169.254.169.254"
    rebind_hostname = "rebinding-test.invalid"
    getaddrinfo_calls: list[str] = []
    connect_calls: list[tuple[str, int]] = []
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection

    def fake_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        # Only the hostname under test is subject to the hostile second
        # answer -- a literal-IP lookup (which the real `create_connection`
        # performs internally even for an address it was already given) is
        # passed straight through, exactly like real DNS: an IP literal is
        # never subject to rebinding because no name resolution occurs.
        if host != rebind_hostname:
            return real_getaddrinfo(host, port, *args, **kwargs)
        getaddrinfo_calls.append(host)
        ip = safe_ip if len(getaddrinfo_calls) == 1 else unsafe_ip
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    def spying_create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        connect_calls.append(address)
        return real_create_connection(address, *args, **kwargs)

    with (
        patch("packages.security.outbound_guard.socket.getaddrinfo", side_effect=fake_getaddrinfo),
        patch(
            "packages.security.outbound_guard.socket.create_connection",
            side_effect=spying_create_connection,
        ),
    ):
        response = guarded_fetch(_url("/ok", host="rebinding-test.invalid"), _ALLOWLISTED_HTTP)

    check(
        "FTF-H006",
        "rebinding: the real local server was genuinely reached through the "
        "mocked-DNS-but-real-socket path",
        response.status == 200 and response.body == b"guarded-ok",
    )
    check(
        "FTF-H006",
        "rebinding: DNS was resolved exactly once for this fetch, never "
        "re-resolved at connect time",
        getaddrinfo_calls == ["rebinding-test.invalid"],
    )
    check(
        "FTF-H006",
        "rebinding: the actual socket connected only to the one address "
        "validated at resolution time, never the second (disallowed) answer "
        "a re-resolution would have returned",
        connect_calls == [(safe_ip, _SERVER_PORT)],
    )
    assert real_getaddrinfo is socket.getaddrinfo, "patch was not restored"


# -- 8. Fault injection: prove the metadata check is genuinely enforced -----

_FAULT_INJECTION_PROBE = """
import sys
import ipaddress
sys.path.insert(0, {root!r})
from packages.security.url_policy import (
    ConnectionPolicy,
    OutboundPolicyError,
    validate_resolved_address,
)

policy = ConnectionPolicy(
    name="probe",
    explicit_private_allowlist=(ipaddress.ip_network("169.254.0.0/16"),),
)
try:
    ip = ipaddress.ip_address("169.254.169.254")
    validate_resolved_address("instance-metadata", ip, policy)
    print("ALLOWED_METADATA")
except OutboundPolicyError:
    print("DENIED_METADATA")
"""


def test_fault_injection_removing_the_metadata_check_is_caught() -> None:
    """Mutate the real module on disk to short-circuit the cloud-metadata
    check, then, in a fresh subprocess (never importlib.reload), confirm
    the metadata address this suite depends on being denied genuinely
    flips to "allowed" once the check is disabled, then restore
    byte-identical."""
    original = URL_POLICY_PATH.read_bytes()
    try:
        src = original.decode("utf-8")
        needle = (
            "    if is_cloud_metadata_address(ip) or hostname.lower() in _CLOUD_METADATA_HOSTNAMES:"
        )
        assert needle in src, "expected cloud-metadata check not found -- module shape changed"
        mutated = src.replace(
            needle,
            "    if False and (is_cloud_metadata_address(ip) or hostname.lower() in "
            "_CLOUD_METADATA_HOSTNAMES):",
        )
        assert mutated != src, "fault injection did not change the file"
        URL_POLICY_PATH.write_text(mutated, encoding="utf-8")

        probe = _FAULT_INJECTION_PROBE.format(root=str(ROOT))
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-H006",
            "fault injection: disabling the cloud-metadata check is genuinely "
            f"caught by this suite (subprocess stdout={result.stdout!r} "
            f"stderr={result.stderr!r})",
            result.returncode == 0 and "ALLOWED_METADATA" in result.stdout,
        )
    finally:
        URL_POLICY_PATH.write_bytes(original)
        restored = URL_POLICY_PATH.read_bytes()
        check(
            "FTF-H006",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-H006 shared outbound URL and credential guard: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H006 shared outbound URL and credential guard: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
