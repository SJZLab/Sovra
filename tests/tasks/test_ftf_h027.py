#!/usr/bin/env python3
"""FTF-H027-P1 -- declared private boundary + metadata-only workflow payloads.

No database. Real loopback sockets (127.0.0.1, 127.0.0.2, ::1) for every
effect test: an allowed sink must really receive the request (positive
control), and a denied sink that would really accept a connection must see
nothing. Criterion ids:

  FTF-H027-B  boundary manifest validation (closed shape, permitted networks)
  FTF-H027-C  destination classification from the manifest, never the caller
  FTF-H027-F  boundary_fetch: profile floor, refusals before any socket, rebinding
  FTF-H027-T  telemetry: only FTF-0903 events reach the collector
  FTF-H027-P  workflow payload codec (closed kinds, canonical, re-validated)
  FTF-H027-W  sanitized failures and workflow identity
  FTF-H027-FAULT  each guard mutated in the real module turns this suite RED

Run: uv run python3 tests/tasks/test_ftf_h027.py
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.canary_secret_generator import generate_canary  # noqa: E402
from packages.security.content_free_telemetry import ContentRefusedError  # noqa: E402
from packages.security.privacy_boundary import (  # noqa: E402
    BOUNDARY_SURFACES,
    BoundaryTransportError,
    DestinationOutsideBoundaryError,
    InvalidBoundaryManifestError,
    InvalidBoundaryRequestError,
    RawTelemetryRefusedError,
    ResolvedOutsideBoundaryError,
    boundary_digest,
    boundary_fetch,
    classify_destination,
    endpoint_connection_policy,
    load_private_boundary,
    parse_private_boundary,
    send_boundary_telemetry,
)
from packages.security.security_profile_enforcement import (  # noqa: E402
    RouteAuthorization,
    RouteBlockedByProfileError,
    RouteNotAuthorizedError,
)
from packages.security.workflow_payload_policy import (  # noqa: E402
    InvalidPayloadSchemaError,
    InvalidWorkflowIdentityError,
    PayloadField,
    PayloadRefusedError,
    PayloadSchema,
    decode_workflow_payload,
    encode_workflow_payload,
    sanitize_workflow_failure,
    validate_workflow_identity,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _code_of(fn, *args: object, **kwargs: object) -> str | None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- the code is what is asserted
        return getattr(type(exc), "code", type(exc).__name__)
    return None


# --- real loopback sinks ---------------------------------------------------


class _Sink:
    def __init__(self, host: str, family: int = socket.AF_INET) -> None:
        self.requests: list[bytes] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                state.requests.append((self.requestline + "\n" + str(self.headers)).encode() + body)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            do_GET = _record  # noqa: N815
            do_POST = _record  # noqa: N815
            do_PUT = _record  # noqa: N815

            def log_message(self, *args: object) -> None:
                return

        class Server(ThreadingHTTPServer):
            address_family = family

        self.server = Server((host, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def _ipv6_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


SINK_A = _Sink("127.0.0.1")  # declared on data
SINK_T = _Sink("127.0.0.1")  # declared on telemetry
SINK_X = _Sink("127.0.0.2")  # never declared; a bypass would really land here
SINK_D = _Sink("127.0.0.1")  # declared on data with a 10/8 network (rebinding target)
IPV6 = _ipv6_available()
SINK_6 = _Sink("::1", socket.AF_INET6) if IPV6 else None


def _ep(
    name: str,
    port: int,
    host: str = "127.0.0.1",
    network: str = "127.0.0.1/32",
    scheme: str = "http",
) -> dict:
    return {"name": name, "scheme": scheme, "host": host, "port": port, "network": network}


def _document() -> dict:
    surfaces: dict[str, list] = {s: [] for s in BOUNDARY_SURFACES}
    surfaces["data"] = [_ep("sink-a", SINK_A.port)]
    surfaces["telemetry"] = [_ep("collector", SINK_T.port)]
    surfaces["preview"] = [_ep("rebind", SINK_D.port, host="localhost", network="10.0.0.0/8")]
    surfaces["history"] = [_ep("temporal", 7233, scheme="tcp")]
    if SINK_6 is not None:
        surfaces["judge"] = [_ep("judge-v6", SINK_6.port, host="::1", network="::1/128")]
    return {
        "schema_version": 1,
        "boundary_id": "h027-test",
        "profile": "zero_egress",
        "deployment": {
            "account": "test-account",
            "cluster": "none",
            "network": "loopback",
            "storage": "none",
            "region": "local",
            "operator_identities": ["op-b", "op-a"],
        },
        "threat_model_exclusions": [
            "authorized_operator_access",
            "model_memorization",
            "privileged_host_compromise",
            "ciphertext_only_capture",
        ],
        "surfaces": surfaces,
    }


BOUNDARY = parse_private_boundary(_document())
ZERO: dict[str, Any] = {"organization_profile": "zero_egress", "project_profile": "zero_egress"}
STD: dict[str, Any] = {"organization_profile": "standard", "project_profile": "standard"}


def test_manifest() -> None:
    committed = load_private_boundary(ROOT / "infra" / "policies" / "private_data_plane.yaml")
    check("FTF-H027-B", "the committed boundary file validates", committed.profile == "zero_egress")
    check(
        "FTF-H027-B",
        "every surface of the committed file is declared",
        tuple(committed.surfaces) == BOUNDARY_SURFACES,
    )
    check(
        "FTF-H027-B",
        "digest is the canonical recomputation",
        boundary_digest(committed) == committed.digest and len(committed.digest) == 64,
    )
    check(
        "FTF-H027-B",
        "operator ids are normalised (sorted)",
        BOUNDARY.deployment.operator_identities == ("op-a", "op-b"),
    )
    reordered = _document()
    reordered["deployment"]["operator_identities"].reverse()
    reordered["threat_model_exclusions"].reverse()
    check(
        "FTF-H027-B",
        "same boundary in another order has the same digest",
        parse_private_boundary(reordered).digest == BOUNDARY.digest,
    )
    moved = _document()
    moved["surfaces"]["data"][0]["port"] = SINK_A.port + 1 if SINK_A.port < 65535 else 1
    check(
        "FTF-H027-B",
        "a changed port changes the digest",
        parse_private_boundary(moved).digest != BOUNDARY.digest,
    )
    for surface in BOUNDARY_SURFACES:
        other = _document()
        other["surfaces"][surface] = other["surfaces"][surface] + [_ep("extra-x", 1)]
        check(
            "FTF-H027-B",
            f"adding an endpoint to {surface} changes the digest",
            parse_private_boundary(other).digest != BOUNDARY.digest,
        )
    for key in ("account", "cluster", "network", "storage", "region"):
        other = _document()
        other["deployment"][key] = "changed"
        check(
            "FTF-H027-B",
            f"deployment {key} is bound into the digest",
            parse_private_boundary(other).digest != BOUNDARY.digest,
        )
    other = _document()
    other["profile"] = "air_gapped"
    check(
        "FTF-H027-B",
        "profile is bound into the digest",
        parse_private_boundary(other).digest != BOUNDARY.digest,
    )
    other = _document()
    other["boundary_id"] = "h027-other"
    check(
        "FTF-H027-B",
        "boundary id is bound into the digest",
        parse_private_boundary(other).digest != BOUNDARY.digest,
    )
    other = _document()
    other["surfaces"]["data"][0]["network"] = "127.0.0.0/8"
    check(
        "FTF-H027-B",
        "network is bound into the digest",
        parse_private_boundary(other).digest != BOUNDARY.digest,
    )
    other = _document()
    other["surfaces"]["data"][0]["name"] = "sink-renamed"
    check(
        "FTF-H027-B",
        "endpoint name is bound into the digest",
        parse_private_boundary(other).digest != BOUNDARY.digest,
    )
    other = _document()
    other["surfaces"]["data"] = [_ep("sink-a", SINK_A.port, scheme="https")]
    check(
        "FTF-H027-B",
        "scheme is bound into the digest",
        parse_private_boundary(other).digest != BOUNDARY.digest,
    )
    other = _document()
    other["surfaces"]["data"] = [
        _ep("sink-a", SINK_A.port, host="127.0.0.5", network="127.0.0.0/8")
    ]
    base = _document()
    base["surfaces"]["data"] = [_ep("sink-a", SINK_A.port, host="127.0.0.1", network="127.0.0.0/8")]
    check(
        "FTF-H027-B",
        "host is bound into the digest",
        parse_private_boundary(other).digest != parse_private_boundary(base).digest,
    )
    other = _document()
    other["deployment"]["operator_identities"] = ["op-a", "op-c"]
    check(
        "FTF-H027-B",
        "operator identities are bound into the digest",
        parse_private_boundary(other).digest != BOUNDARY.digest,
    )
    valid_nets = (
        "10.1.0.0/16",
        "172.16.0.0/12",
        "192.168.4.0/24",
        "127.0.0.0/8",
        "::1/128",
        "fd12:3456::/32",
        "fc00::/8",
    )
    for net in valid_nets:
        doc = _document()
        first = str(next(ipaddress.ip_network(net).hosts(), ipaddress.ip_network(net)[0]))
        if net == "::1/128":
            first = "::1"
        doc["surfaces"]["source"] = [_ep("ok-net", 443, host=first, network=net, scheme="https")]
        check(
            "FTF-H027-B",
            f"permitted network {net} is accepted",
            not _raises(Exception, parse_private_boundary, doc),
        )
    doc = _document()
    doc["surfaces"]["source"] = [
        _ep("svc", 443, host="svc.sovra.internal", network="10.0.0.0/8", scheme="https")
    ]
    check(
        "FTF-H027-B",
        "a lowercase DNS-name endpoint is accepted",
        not _raises(Exception, parse_private_boundary, doc),
    )
    for port in (1, 65535):
        doc = _document()
        doc["surfaces"]["source"] = [_ep("edge", port)]
        check(
            "FTF-H027-B",
            f"port {port} is accepted",
            not _raises(Exception, parse_private_boundary, doc),
        )
    doc = _document()
    doc["surfaces"]["source"] = [_ep("e%d" % i, 1000 + i) for i in range(32)]
    check(
        "FTF-H027-B",
        "32 endpoints on one surface are accepted",
        not _raises(Exception, parse_private_boundary, doc),
    )
    doc = _document()
    doc["deployment"]["operator_identities"] = ["o%d" % i for i in range(32)]
    check(
        "FTF-H027-B",
        "32 operator identities are accepted",
        not _raises(Exception, parse_private_boundary, doc),
    )
    doc = _document()
    doc["surfaces"]["training"] = [_ep("sink-a", SINK_A.port)]
    check(
        "FTF-H027-B",
        "one endpoint declared identically on two surfaces is accepted",
        not _raises(Exception, parse_private_boundary, doc),
    )

    def bad(description: str, mutate) -> None:
        doc = _document()
        mutate(doc)
        check(
            "FTF-H027-B",
            f"refused: {description}",
            _raises(InvalidBoundaryManifestError, parse_private_boundary, doc),
        )

    bad("unknown top-level key", lambda d: d.__setitem__("extra", 1))
    bad("missing top-level key", lambda d: d.pop("deployment"))
    bad("non-dict document", lambda d: d.clear())
    bad("schema_version 2", lambda d: d.__setitem__("schema_version", 2))
    bad("schema_version True", lambda d: d.__setitem__("schema_version", True))
    bad("schema_version '1'", lambda d: d.__setitem__("schema_version", "1"))
    bad("unknown profile", lambda d: d.__setitem__("profile", "private"))
    bad("profile non-string", lambda d: d.__setitem__("profile", ["zero_egress"]))
    bad("boundary id uppercase", lambda d: d.__setitem__("boundary_id", "Bad"))
    bad("boundary id with newline", lambda d: d.__setitem__("boundary_id", "ok\n"))
    bad("unknown deployment key", lambda d: d["deployment"].__setitem__("x", "y"))
    bad("missing deployment key", lambda d: d["deployment"].pop("region"))
    bad("deployment text empty", lambda d: d["deployment"].__setitem__("account", ""))
    bad("deployment text with newline", lambda d: d["deployment"].__setitem__("account", "a\nb"))
    bad("deployment text too long", lambda d: d["deployment"].__setitem__("account", "a" * 129))
    bad("deployment text non-string", lambda d: d["deployment"].__setitem__("cluster", 3))
    bad("no operator identity", lambda d: d["deployment"].__setitem__("operator_identities", []))
    bad(
        "operator identities not a list",
        lambda d: d["deployment"].__setitem__("operator_identities", "op-a"),
    )
    bad(
        "33 operator identities",
        lambda d: d["deployment"].__setitem__(
            "operator_identities", ["o%d" % i for i in range(33)]
        ),
    )
    bad(
        "duplicate operator identity",
        lambda d: d["deployment"].__setitem__("operator_identities", ["op-a", "op-a"]),
    )
    bad(
        "operator identity malformed",
        lambda d: d["deployment"].__setitem__("operator_identities", ["Op A"]),
    )
    bad(
        "an exclusion missing (operator access dropped)",
        lambda d: d["threat_model_exclusions"].remove("authorized_operator_access"),
    )
    bad(
        "an exclusion missing (memorization dropped)",
        lambda d: d["threat_model_exclusions"].remove("model_memorization"),
    )
    bad("no exclusions", lambda d: d.__setitem__("threat_model_exclusions", []))
    bad("unknown exclusion", lambda d: d["threat_model_exclusions"].append("none"))
    bad(
        "duplicate exclusion",
        lambda d: d.__setitem__(
            "threat_model_exclusions", ["model_memorization"] * 2 + d["threat_model_exclusions"][:2]
        ),
    )
    bad(
        "exclusion non-string",
        lambda d: d.__setitem__("threat_model_exclusions", d["threat_model_exclusions"][:3] + [1]),
    )
    bad("unknown surface", lambda d: d["surfaces"].__setitem__("serving", []))
    bad("missing surface", lambda d: d["surfaces"].pop("browser"))
    bad("surface not a list", lambda d: d["surfaces"].__setitem__("source", {}))
    bad(
        "33 endpoints on a surface",
        lambda d: d["surfaces"].__setitem__(
            "source", [_ep("e%d" % i, 1000 + i) for i in range(33)]
        ),
    )
    bad("endpoint unknown key", lambda d: d["surfaces"]["data"][0].__setitem__("tls", True))
    bad("endpoint missing key", lambda d: d["surfaces"]["data"][0].pop("network"))
    bad("endpoint not a mapping", lambda d: d["surfaces"].__setitem__("source", ["http://x"]))
    bad("endpoint name malformed", lambda d: d["surfaces"]["data"][0].__setitem__("name", "A B"))
    bad("scheme ftp", lambda d: d["surfaces"]["data"][0].__setitem__("scheme", "ftp"))
    bad("scheme uppercase", lambda d: d["surfaces"]["data"][0].__setitem__("scheme", "HTTP"))
    bad("port 0", lambda d: d["surfaces"]["data"][0].__setitem__("port", 0))
    bad("port 65536", lambda d: d["surfaces"]["data"][0].__setitem__("port", 65536))
    bad("port bool", lambda d: d["surfaces"]["data"][0].__setitem__("port", True))
    bad("port string", lambda d: d["surfaces"]["data"][0].__setitem__("port", "8080"))
    for net, why in (
        ("0.0.0.0/0", "IPv4 default route"),
        ("::/0", "IPv6 default route"),
        ("8.8.8.0/24", "public IPv4"),
        ("169.254.0.0/16", "link-local (metadata)"),
        ("169.254.169.254/32", "cloud metadata IPv4"),
        ("100.100.100.200/32", "Alibaba metadata"),
        ("100.64.0.0/10", "CGNAT"),
        ("fd00:ec2::254/128", "AWS IMDSv6"),
        ("::ffff:127.0.0.1/128", "IPv4-mapped IPv6"),
        ("64:ff9b::/96", "NAT64"),
        ("fe80::/10", "IPv6 link-local"),
        ("fc00::/7", "ULA range containing AWS IMDSv6"),
        ("2001:db8::/32", "public-range IPv6"),
        ("10.0.0.0/7", "wider than RFC 1918"),
        ("127.0.0.1/24", "non-canonical CIDR"),
        ("not-a-cidr", "garbage"),
    ):
        if net == "fd00:ec2::254/128":
            # inside fc00::/7: only the metadata rule can refuse it
            bad(
                f"network {why}",
                lambda d, n=net: d["surfaces"].__setitem__(
                    "source", [_ep("m", 80, host="fd00:ec2::254", network=n)]
                ),
            )
            continue
        try:
            parsed_net = ipaddress.ip_network(net)
            inside = str(parsed_net[1] if parsed_net.num_addresses > 1 else parsed_net[0])
        except ValueError:
            inside = "127.0.0.1"
        # The host sits inside the bad network, so only the network rule refuses it.
        bad(
            f"network {why}",
            lambda d, n=net, h=inside: d["surfaces"]["data"][0].update(network=n, host=h),
        )
    bad("network non-string", lambda d: d["surfaces"]["data"][0].__setitem__("network", 10))
    bad(
        "host outside its network",
        lambda d: d["surfaces"]["data"][0].__setitem__("host", "127.0.0.2"),
    )
    bad(
        "host non-canonical IPv6",
        lambda d: d["surfaces"].__setitem__(
            "source", [_ep("v6", 80, host="0:0::1", network="::1/128")]
        ),
    )
    bad(
        "host integer-encoded IPv4",
        lambda d: d["surfaces"]["data"][0].__setitem__("host", "2130706433"),
    )
    bad(
        "host octal-encoded IPv4",
        lambda d: d["surfaces"]["data"][0].__setitem__("host", "0177.0.0.1"),
    )
    bad(
        "host uppercase DNS", lambda d: d["surfaces"]["preview"][0].__setitem__("host", "LocalHost")
    )
    bad("host empty", lambda d: d["surfaces"]["preview"][0].__setitem__("host", ""))
    bad("host non-string", lambda d: d["surfaces"]["preview"][0].__setitem__("host", 1))
    bad("host with trailing dot", lambda d: d["surfaces"]["preview"][0].__setitem__("host", "svc."))
    bad(
        "host bracketed IPv6",
        lambda d: d["surfaces"].__setitem__(
            "source", [_ep("v6", 80, host="[::1]", network="::1/128")]
        ),
    )
    for name in ("metadata", "metadata.google.internal", "metadata.goog", "metadata.azure.com"):
        bad(
            f"host names metadata service {name}",
            lambda d, n=name: d["surfaces"]["preview"][0].__setitem__("host", n),
        )
    bad(
        "duplicate endpoint name on one surface",
        lambda d: d["surfaces"]["data"].append(_ep("sink-a", 1)),
    )
    bad(
        "duplicate destination on one surface",
        lambda d: d["surfaces"]["data"].append(_ep("sink-b", SINK_A.port)),
    )
    bad(
        "one name, two meanings across surfaces",
        lambda d: d["surfaces"]["training"].append(_ep("sink-a", 1)),
    )
    bad("non-string key", lambda d: d["surfaces"].__setitem__(1, []))
    bad(
        "deployment is a list of its key names",
        lambda d: d.__setitem__("deployment", list(d["deployment"])),
    )
    bad("host is a list", lambda d: d["surfaces"]["preview"][0].__setitem__("host", ["x"]))
    bad(
        "host uppercase IPv6 literal",
        lambda d: d["surfaces"].__setitem__(
            "source", [_ep("v6u", 80, host="fd00::A", network="fd00::/16")]
        ),
    )
    bad(
        "unhashable exclusion",
        lambda d: d.__setitem__(
            "threat_model_exclusions", d["threat_model_exclusions"][:3] + [["model_memorization"]]
        ),
    )
    check(
        "FTF-H027-B",
        "unreadable file is refused",
        _raises(
            InvalidBoundaryManifestError, load_private_boundary, ROOT / "no-such-boundary.yaml"
        ),
    )
    tmp = ROOT / ".h027-bad.yaml"
    try:
        tmp.write_text("a: [unclosed", encoding="utf-8")
        check(
            "FTF-H027-B",
            "invalid YAML is refused",
            _raises(InvalidBoundaryManifestError, load_private_boundary, tmp),
        )
        tmp.write_bytes(b"\xff\xfe")
        check(
            "FTF-H027-B",
            "undecodable file is refused",
            _raises(InvalidBoundaryManifestError, load_private_boundary, tmp),
        )
    finally:
        tmp.unlink(missing_ok=True)
    check(
        "FTF-H027-B",
        "boundary_digest refuses a non-boundary",
        _raises(InvalidBoundaryRequestError, boundary_digest, object()),
    )
    msg = ""
    try:
        doc = _document()
        doc["surfaces"]["data"][0]["host"] = "127.0.0.9"
        parse_private_boundary(doc)
    except InvalidBoundaryManifestError as exc:
        msg = str(exc)
    check(
        "FTF-H027-B",
        "manifest errors never echo the offending value",
        "127.0.0.9" not in msg and bool(msg),
    )
    check(
        "FTF-H027-B",
        "manifest error code is stable",
        InvalidBoundaryManifestError.code == "boundary_manifest_invalid",
    )


def test_classification() -> None:
    a = f"http://127.0.0.1:{SINK_A.port}/x"
    c = classify_destination(BOUNDARY, "data", a)
    check(
        "FTF-H027-C",
        "declared (scheme, host, port) on its surface is internal",
        c.destination.kind == "internal" and c.endpoint is not None and c.endpoint.name == "sink-a",
    )
    for url, why in (
        (a, "declared endpoint used on another surface (judge)"),
        (f"http://127.0.0.2:{SINK_X.port}/x", "undeclared host"),
        (f"https://127.0.0.1:{SINK_A.port}/x", "other scheme"),
        (f"http://127.0.0.1:{SINK_A.port + 1 if SINK_A.port < 65535 else 1}/x", "other port"),
        ("http://127.0.0.1/x", "implicit port"),
        (f"http://2130706433:{SINK_A.port}/x", "integer-encoded host"),
        (f"http://0177.0.0.1:{SINK_A.port}/x", "octal-encoded host"),
        (f"http://[::ffff:127.0.0.1]:{SINK_A.port}/x", "IPv4-mapped IPv6 host"),
        (f"http://u:p@127.0.0.1:{SINK_A.port}/x", "userinfo"),
        (f"http://LOCALHOST:{SINK_D.port}/x", "mixed-case DNS host"),
        ("http://169.254.169.254:80/latest/meta-data", "cloud metadata"),
        ("http://[fd00:ec2::254]:80/latest", "IPv6 cloud metadata"),
        (f"ftp://127.0.0.1:{SINK_A.port}/x", "ftp"),
        ("http://127.0.0.1:7233/", "tcp-only endpoint (not fetchable)"),
        ("http://127.0.0.1:99999/", "invalid port"),
        ("", "empty url"),
        ("x" * 3000, "oversized url"),
    ):
        surface = "judge" if why.startswith("declared endpoint used") else "data"
        if why.startswith("tcp-only"):
            surface = "history"
        if why.startswith("mixed-case"):
            surface = "preview"
        got = classify_destination(BOUNDARY, surface, url)
        check(
            "FTF-H027-C",
            f"external: {why}",
            got.destination.kind == "external" and got.endpoint is None,
        )
    pad = f"http://127.0.0.1:{SINK_A.port}/"
    long_ok = pad + "a" * (2048 - len(pad))
    check(
        "FTF-H027-C",
        "a 2048-character declared URL is internal",
        classify_destination(BOUNDARY, "data", long_ok).endpoint is not None,
    )
    check(
        "FTF-H027-C",
        "a 2049-character URL is external",
        classify_destination(BOUNDARY, "data", long_ok + "a").endpoint is None,
    )
    from packages.security.privacy_boundary import BoundaryEndpoint as _BE

    forged_ep = _BE("forged", "HTTP ", "127.0.0.1", 80, ipaddress.ip_network("127.0.0.1/32"))
    check(
        "FTF-H027-C",
        "a forged endpoint with a non-canonical scheme has no fetch policy",
        _raises(InvalidBoundaryRequestError, endpoint_connection_policy, forged_ep),
    )
    check(
        "FTF-H027-C",
        "non-string url classifies external",
        classify_destination(BOUNDARY, "data", 5).endpoint is None,  # type: ignore[arg-type]
    )
    check(
        "FTF-H027-C",
        "lowercase DNS host as declared is internal",
        classify_destination(BOUNDARY, "preview", f"http://localhost:{SINK_D.port}/").endpoint
        is not None,
    )
    if SINK_6 is not None:
        check(
            "FTF-H027-C",
            "declared IPv6 literal is internal",
            classify_destination(BOUNDARY, "judge", f"http://[::1]:{SINK_6.port}/").endpoint
            is not None,
        )
    check(
        "FTF-H027-C",
        "unknown surface refused",
        _raises(InvalidBoundaryRequestError, classify_destination, BOUNDARY, "serving", a),
    )
    check(
        "FTF-H027-C",
        "non-boundary refused",
        _raises(InvalidBoundaryRequestError, classify_destination, object(), "data", a),
    )
    forged = copy.copy(BOUNDARY)
    object.__setattr__(forged, "profile", "standard")
    check(
        "FTF-H027-C",
        "a boundary altered after validation is refused",
        _raises(InvalidBoundaryRequestError, classify_destination, forged, "data", a),
    )
    policy = endpoint_connection_policy(c.endpoint)  # type: ignore[arg-type]
    check(
        "FTF-H027-C",
        "endpoint policy is exactly one scheme, port and network",
        policy.allowed_schemes == frozenset({"http"})
        and policy.allowed_ports == frozenset({SINK_A.port})
        and policy.explicit_private_allowlist == (ipaddress.ip_network("127.0.0.1/32"),)
        and policy.allow_redirects is False
        and policy.max_redirects == 0,
    )
    tcp = BOUNDARY.surfaces["history"][0]
    check(
        "FTF-H027-C",
        "a tcp endpoint has no fetch policy",
        _raises(InvalidBoundaryRequestError, endpoint_connection_policy, tcp),
    )


def test_fetch() -> None:
    a = f"http://127.0.0.1:{SINK_A.port}/obj"
    before = len(SINK_A.requests)
    result = boundary_fetch(BOUNDARY, "data", a, method="POST", body=b"in-boundary", **ZERO)
    check(
        "FTF-H027-F",
        "declared endpoint really receives the request (positive control)",
        result.status == 200
        and result.body == b"ok"
        and len(SINK_A.requests) == before + 1
        and SINK_A.requests[-1].endswith(b"in-boundary"),
    )
    check(
        "FTF-H027-F",
        "result names the endpoint and the decision",
        result.endpoint_name == "sink-a"
        and result.decision.allowed
        and result.decision.effective_profile == "zero_egress"
        and result.surface == "data",
    )
    check(
        "FTF-H027-F",
        "the request path is the URL's own path",
        SINK_A.requests[-1].startswith(b"POST /obj HTTP/1.1"),
    )
    boundary_fetch(BOUNDARY, "data", a + "?q=1", **ZERO)
    check(
        "FTF-H027-F",
        "the query string is sent",
        SINK_A.requests[-1].startswith(b"GET /obj?q=1 HTTP/1.1"),
    )
    boundary_fetch(BOUNDARY, "data", f"http://127.0.0.1:{SINK_A.port}", **ZERO)
    check(
        "FTF-H027-F",
        "an empty path is sent as /",
        SINK_A.requests[-1].startswith(b"GET / HTTP/1.1"),
    )
    boundary_fetch(BOUNDARY, "data", a, method="POST", body=b"in-boundary", **ZERO)
    check(
        "FTF-H027-F",
        "the guard sends Connection: close",
        b"Connection: close" in SINK_A.requests[-1],
    )
    std = boundary_fetch(BOUNDARY, "data", a, **STD)
    check(
        "FTF-H027-F",
        "the boundary profile is a floor (standard request runs as zero_egress)",
        std.decision.effective_profile == "zero_egress",
    )

    x_before = len(SINK_X.requests)
    x = f"http://127.0.0.2:{SINK_X.port}/exfil"
    check(
        "FTF-H027-F",
        "undeclared host under zero_egress is blocked by FTF-0901",
        _code_of(boundary_fetch, BOUNDARY, "data", x, body=b"leak", method="POST", **ZERO)
        == RouteBlockedByProfileError.code,
    )
    check(
        "FTF-H027-F",
        "a weaker project setting cannot lower the boundary profile",
        _raises(RouteBlockedByProfileError, boundary_fetch, BOUNDARY, "data", x, **STD),
    )
    loose = _document()
    loose["profile"] = "standard"
    standard_boundary = parse_private_boundary(loose)
    check(
        "FTF-H027-F",
        "standard boundary, no authorization: route not authorized",
        _raises(RouteNotAuthorizedError, boundary_fetch, standard_boundary, "judge", x, **STD),
    )
    auth = RouteAuthorization(
        provider="undeclared", surfaces=frozenset({"judge"}), approved_by="admin"
    )
    check(
        "FTF-H027-F",
        "even an authorized external route is never performed by the boundary",
        _code_of(boundary_fetch, standard_boundary, "judge", x, authorization=auth, **STD)
        == DestinationOutsideBoundaryError.code,
    )
    check(
        "FTF-H027-F",
        "a stricter organization profile than the boundary still applies",
        _raises(
            RouteBlockedByProfileError,
            boundary_fetch,
            standard_boundary,
            "judge",
            x,
            organization_profile="zero_egress",
            project_profile="standard",
            authorization=auth,
        ),
    )
    check(
        "FTF-H027-F",
        "a stricter job profile still applies",
        _raises(
            RouteBlockedByProfileError,
            boundary_fetch,
            standard_boundary,
            "judge",
            x,
            organization_profile="standard",
            project_profile="standard",
            job_profile="air_gapped",
            authorization=auth,
        ),
    )
    check(
        "FTF-H027-F",
        "declared data endpoint used for judge is refused",
        _raises(RouteBlockedByProfileError, boundary_fetch, BOUNDARY, "judge", a, **ZERO),
    )
    for url in (
        "http://169.254.169.254:80/latest/meta-data/",
        "http://[fd00:ec2::254]:80/latest/meta-data/",
        "http://[::ffff:169.254.169.254]:80/",
        f"http://[::ffff:127.0.0.2]:{SINK_X.port}/",
    ):
        check(
            "FTF-H027-F",
            f"metadata / mapped destination refused: {url.split('/')[2]}",
            _raises(RouteBlockedByProfileError, boundary_fetch, BOUNDARY, "data", url, **ZERO),
        )
    if SINK_6 is not None:
        v6_before = len(SINK_6.requests)
        v6 = boundary_fetch(BOUNDARY, "judge", f"http://[::1]:{SINK_6.port}/judge", **ZERO)
        check(
            "FTF-H027-F",
            "declared IPv6 internal judge really receives the request",
            v6.status == 200 and len(SINK_6.requests) == v6_before + 1,
        )
        check(
            "FTF-H027-F",
            "the IPv6 judge on the data surface is refused",
            _raises(
                RouteBlockedByProfileError,
                boundary_fetch,
                BOUNDARY,
                "data",
                f"http://[::1]:{SINK_6.port}/judge",
                **ZERO,
            ),
        )
    elif os.environ.get("FTFH027_ALLOW_NO_IPV6") == "1":
        # Local sandboxes with no IPv6 kernel support only. CI never sets this
        # (the gate step asserts it is unset), so there the missing path is RED.
        print("FTF-H027-F: IPv6 loopback unavailable -- IPv6 path NOT EXERCISED (local run)")
    else:
        FAILURES.append("FAIL [FTF-H027-F] IPv6 loopback unavailable: IPv6 path not exercised")
    d_before = len(SINK_D.requests)
    check(
        "FTF-H027-F",
        "a declared name resolving outside its network is refused (rebinding)",
        _code_of(boundary_fetch, BOUNDARY, "preview", f"http://localhost:{SINK_D.port}/", **ZERO)
        == ResolvedOutsideBoundaryError.code,
    )
    check("FTF-H027-F", "the rebinding target saw nothing", len(SINK_D.requests) == d_before)
    # H006 admits every public address, so only the boundary's own containment
    # check refuses a declared endpoint that lands on one. No socket is opened.
    from packages.security.privacy_boundary import BoundaryEndpoint, _pinned_address

    public = BoundaryEndpoint("forged", "http", "8.8.8.8", 80, ipaddress.ip_network("10.0.0.0/8"))
    check(
        "FTF-H027-F",
        "a declared endpoint landing on a public address is refused",
        _code_of(_pinned_address, "http://8.8.8.8:80/", public, endpoint_connection_policy(public))
        == ResolvedOutsideBoundaryError.code,
    )
    meta = BoundaryEndpoint(
        "forged-m", "http", "169.254.169.254", 80, ipaddress.ip_network("169.254.0.0/16")
    )
    check(
        "FTF-H027-F",
        "a forged metadata endpoint is refused at pinning",
        _code_of(
            _pinned_address, "http://169.254.169.254:80/", meta, endpoint_connection_policy(meta)
        )
        == ResolvedOutsideBoundaryError.code,
    )
    check(
        "FTF-H027-F", "no denied flow reached the undeclared sink", len(SINK_X.requests) == x_before
    )

    # A declared endpoint whose socket is closed stays a visible failure.
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    dead_port = closed.getsockname()[1]
    closed.close()
    doc = _document()
    doc["surfaces"]["source"] = [_ep("dead", dead_port)]
    dead = parse_private_boundary(doc)
    check(
        "FTF-H027-F",
        "refused connection is a transport failure, never success",
        _code_of(boundary_fetch, dead, "source", f"http://127.0.0.1:{dead_port}/", **ZERO)
        == BoundaryTransportError.code,
    )
    doc = _document()
    doc["surfaces"]["source"] = [_ep("nxd", 80, host="h027-nxdomain.invalid", network="10.0.0.0/8")]
    nx = parse_private_boundary(doc)
    check(
        "FTF-H027-F",
        "an unresolvable declared name is a transport failure",
        _code_of(boundary_fetch, nx, "source", "http://h027-nxdomain.invalid:80/", **ZERO)
        == BoundaryTransportError.code,
    )

    def invalid(description: str, **overrides: object) -> None:
        kwargs: dict[str, object] = {"method": "GET", **ZERO}
        kwargs.update(overrides)
        url = kwargs.pop("url", a)
        surface = kwargs.pop("surface", "data")
        boundary = kwargs.pop("boundary", BOUNDARY)
        check(
            "FTF-H027-F",
            f"invalid request refused: {description}",
            _raises(InvalidBoundaryRequestError, boundary_fetch, boundary, surface, url, **kwargs),
        )

    invalid("unknown method", method="DELETE")
    invalid("lowercase method", method="get")
    invalid("non-bytes body", body="text")
    invalid("oversized body", body=b"x" * (1_048_576 + 1))
    invalid("zero timeout", timeout=0)
    invalid("timeout above 60s", timeout=61)
    invalid("bool timeout", timeout=True)
    invalid("headers with CRLF", headers={"X": "a\r\nInjected: 1"})
    invalid("headers with NUL key", headers={"X\x00": "a"})
    invalid("headers not a dict", headers=[("X", "a")])
    invalid("33 headers", headers={f"X-{i}": "v" for i in range(33)})
    invalid("non-string header value", headers={"X": 1})
    invalid("unknown organization profile", organization_profile="private")
    invalid("unknown project profile", project_profile=None)
    invalid("unknown job profile", job_profile="loose")
    invalid("non-string url", url=b"http://x")
    invalid("authorization of the wrong type", authorization={"provider": "x"})
    invalid("unknown surface", surface="serving")
    invalid("non-boundary", boundary=object())
    ok_body = boundary_fetch(
        BOUNDARY,
        "data",
        a,
        method="PUT",
        body=b"x" * 1_048_576,
        headers={f"X-{i}": "v" for i in range(32)},
        timeout=60,
        **ZERO,
    )
    check(
        "FTF-H027-F",
        "bounds are inclusive (1 MiB body, 32 headers, 60 s, PUT)",
        ok_body.status == 200,
    )
    big = _Sink("127.0.0.1")

    class BigHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(1_048_577))
            self.end_headers()
            self.wfile.write(b"x" * 1_048_577)

        def log_message(self, *args: object) -> None:
            return

    big.server.RequestHandlerClass = BigHandler
    doc = _document()
    doc["surfaces"]["source"] = [_ep("big", big.port)]
    big_boundary = parse_private_boundary(doc)
    check(
        "FTF-H027-F",
        "an oversized response is refused, not truncated into success",
        _code_of(boundary_fetch, big_boundary, "source", f"http://127.0.0.1:{big.port}/", **ZERO)
        == BoundaryTransportError.code,
    )
    msg = ""
    try:
        boundary_fetch(BOUNDARY, "data", x, **ZERO)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
    check(
        "FTF-H027-F",
        "refusals never echo host or port",
        "127.0.0.2" not in msg and str(SINK_X.port) not in msg,
    )


def test_telemetry() -> None:
    t = f"http://127.0.0.1:{SINK_T.port}/v1/events"
    before = len(SINK_T.requests)
    res = send_boundary_telemetry(
        BOUNDARY,
        t,
        "log",
        {"level": "info", "message_code": "job_started", "job_id": "job-1"},
        **ZERO,
    )
    arrived = SINK_T.requests[-1] if len(SINK_T.requests) == before + 1 else b""
    check(
        "FTF-H027-T",
        "content-free event reaches the declared collector (positive control)",
        res.status == 200
        and arrived.endswith(
            b'{"fields":{"job_id":"job-1","level":"info","message_code":"job_started"},"kind":"log"}'
        ),
    )
    marker = generate_canary()
    before = len(SINK_T.requests)
    check(
        "FTF-H027-T",
        "an event carrying content is refused before any socket",
        _raises(
            ContentRefusedError,
            send_boundary_telemetry,
            BOUNDARY,
            t,
            "log",
            {"level": "info", "message_code": "job_started", "job_id": marker.value + " x"},
            **ZERO,
        ),
    )
    check(
        "FTF-H027-T",
        "a raw body to the permitted collector is refused",
        _code_of(
            boundary_fetch,
            BOUNDARY,
            "telemetry",
            t,
            method="POST",
            body=marker.value.encode(),
            **ZERO,
        )
        == RawTelemetryRefusedError.code,
    )
    check(
        "FTF-H027-T",
        "telemetry to an undeclared collector is refused",
        _raises(
            RouteBlockedByProfileError,
            send_boundary_telemetry,
            BOUNDARY,
            f"http://127.0.0.2:{SINK_X.port}/v1/events",
            "log",
            {"level": "info", "message_code": "job_started"},
            **ZERO,
        ),
    )
    check(
        "FTF-H027-T",
        "telemetry to a data endpoint is refused",
        _raises(
            RouteBlockedByProfileError,
            send_boundary_telemetry,
            BOUNDARY,
            f"http://127.0.0.1:{SINK_A.port}/",
            "log",
            {"level": "info", "message_code": "job_started"},
            **ZERO,
        ),
    )
    check(
        "FTF-H027-T",
        "the collector never saw the canary",
        len(SINK_T.requests) == before
        and all(marker.value.encode() not in r for r in SINK_T.requests),
    )
    from packages.security.content_free_telemetry import ContentRef

    send_boundary_telemetry(
        BOUNDARY,
        t,
        "workflow",
        {
            "workflow_id": "wf-1",
            "task_id": "t-1",
            "state": "failed",
            "timing_ms": 1.5,
            "failure_ref": ContentRef("ref-1", "key-1"),
        },
        **ZERO,
    )
    check(
        "FTF-H027-T",
        "content refs serialise as their two ids only",
        b'"failure_ref":{"encryption_key_id":"key-1","ref_id":"ref-1"}' in SINK_T.requests[-1],
    )


SCHEMA = PayloadSchema(
    "train_step",
    {
        "job_id": PayloadField("uuid"),
        "dataset_sha256": PayloadField("sha256"),
        "rows": PayloadField("count"),
        "resumed": PayloadField("bool", required=False),
        "state": PayloadField("enum", values=("queued", "running")),
    },
)
JOB = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
GOOD = {"job_id": JOB, "dataset_sha256": "a" * 64, "rows": 3, "state": "queued"}


def test_payload() -> None:
    data = encode_workflow_payload(SCHEMA, GOOD)
    check(
        "FTF-H027-P",
        "canonical encoding",
        data
        == (
            '{"fields":{"dataset_sha256":"'
            + "a" * 64
            + '","job_id":"'
            + JOB
            + '","rows":3,"state":"queued"},"schema":"train_step","v":1}'
        ).encode(),
    )
    check(
        "FTF-H027-P", "round trip re-validates", dict(decode_workflow_payload(SCHEMA, data)) == GOOD
    )
    check(
        "FTF-H027-P",
        "optional bool accepted",
        b'"resumed":false' in encode_workflow_payload(SCHEMA, {**GOOD, "resumed": False}),
    )
    check(
        "FTF-H027-P",
        "count bounds inclusive",
        not _raises(Exception, encode_workflow_payload, SCHEMA, {**GOOD, "rows": 0})
        and not _raises(Exception, encode_workflow_payload, SCHEMA, {**GOOD, "rows": 10**12}),
    )
    marker = generate_canary()
    for name, value, why in (
        ("job_id", marker.value, "canary in a uuid field"),
        ("job_id", JOB.upper(), "uppercase uuid"),
        ("job_id", JOB + "\n", "uuid with trailing newline"),
        ("job_id", "{" + JOB + "}", "braced uuid"),
        ("dataset_sha256", "A" * 64, "uppercase digest"),
        ("dataset_sha256", "a" * 63, "short digest"),
        ("dataset_sha256", marker.value.encode().hex()[:64] + "x", "non-hex digest"),
        ("rows", -1, "negative count"),
        ("rows", 10**12 + 1, "count over bound"),
        ("rows", True, "bool as count"),
        ("rows", 1.0, "float count"),
        ("rows", "3", "string count"),
        ("resumed", 1, "int as bool"),
        ("state", "done", "enum value not declared"),
        ("state", "QUEUED", "enum case"),
        ("state", ["queued"], "enum non-string"),
        ("job_id", 5, "non-string uuid"),
    ):
        check(
            "FTF-H027-P",
            f"refused: {why}",
            _raises(PayloadRefusedError, encode_workflow_payload, SCHEMA, {**GOOD, name: value}),
        )
    check(
        "FTF-H027-P",
        "a declared field spelled in another case is refused",
        _raises(PayloadRefusedError, encode_workflow_payload, SCHEMA, {**GOOD, "ROWS": 3}),
    )
    check(
        "FTF-H027-P",
        "an uppercase field kind is refused",
        _raises(InvalidPayloadSchemaError, PayloadSchema, "s", {"a": PayloadField("BOOL")}),
    )
    check(
        "FTF-H027-P",
        "unknown field refused (e.g. raw prompt)",
        _raises(PayloadRefusedError, encode_workflow_payload, SCHEMA, {**GOOD, "prompt": "x"}),
    )
    check(
        "FTF-H027-P",
        "missing required field refused",
        _raises(
            PayloadRefusedError,
            encode_workflow_payload,
            SCHEMA,
            {k: v for k, v in GOOD.items() if k != "rows"},
        ),
    )
    check(
        "FTF-H027-P",
        "non-dict fields refused",
        _raises(PayloadRefusedError, encode_workflow_payload, SCHEMA, list(GOOD.items())),
    )
    check(
        "FTF-H027-P",
        "non-schema refused",
        _raises(InvalidPayloadSchemaError, encode_workflow_payload, object(), GOOD),
    )
    check(
        "FTF-H027-P",
        "error message never echoes the value",
        marker.value
        not in (str(_catch(encode_workflow_payload, SCHEMA, {**GOOD, "job_id": marker.value}))),
    )

    def bad_decode(why: str, raw: object) -> None:
        check(
            "FTF-H027-P",
            f"decode refused: {why}",
            _raises(PayloadRefusedError, decode_workflow_payload, SCHEMA, raw),
        )

    doc = json.loads(data)
    bad_decode("non-bytes", data.decode())
    bad_decode("empty", b"")
    bad_decode("oversized", b" " * 16_385)
    bad_decode("not JSON", b"{")
    bad_decode("non-ASCII", '{"a":"é"}'.encode())
    bad_decode("JSON list", b"[]")
    bad_decode("JSON list naming fields", b'["fields"]')
    bad_decode("envelope without fields", b'{"schema":"train_step","v":1}')
    bad_decode("extra envelope key", json.dumps({**doc, "x": 1}).encode())
    bad_decode(
        "other schema",
        json.dumps({**doc, "schema": "other"}, sort_keys=True, separators=(",", ":")).encode(),
    )
    bad_decode("version 2", data.replace(b'"v":1', b'"v":2'))
    bad_decode("version true", data.replace(b'"v":1', b'"v":true'))
    bad_decode("tampered value", data.replace(b'"rows":3', b'"rows":-3'))
    bad_decode("smuggled field", data.replace(b'"rows":3', b'"rows":3,"note":"x"'))
    bad_decode("non-canonical spacing", data.replace(b'"rows":3', b'"rows": 3'))
    bad_decode("fields not a dict", json.dumps({**doc, "fields": []}).encode())
    check(
        "FTF-H027-P",
        "decode bound inclusive (16384 bytes is length-accepted)",
        not isinstance(
            _catch(decode_workflow_payload, SCHEMA, data + b" " * (16_384 - len(data))),
            PayloadRefusedError,
        )
        or "canonical"
        in str(_catch(decode_workflow_payload, SCHEMA, data + b" " * (16_384 - len(data)))),
    )
    check(
        "FTF-H027-P",
        "decode refuses non-schema",
        _raises(InvalidPayloadSchemaError, decode_workflow_payload, object(), data),
    )

    def bad_schema(why: str, name: object, fields: object) -> None:
        check(
            "FTF-H027-P",
            f"schema refused: {why}",
            _raises(InvalidPayloadSchemaError, PayloadSchema, name, fields),
        )

    bad_schema("name uppercase", "Train", {"a": PayloadField("bool")})
    bad_schema("name non-string", 1, {"a": PayloadField("bool")})
    bad_schema("no fields", "s", {})
    bad_schema("fields not a mapping", "s", [("a", PayloadField("bool"))])
    bad_schema("33 fields", "s", {f"f{i}": PayloadField("bool") for i in range(33)})
    bad_schema("field name malformed", "s", {"A-b": PayloadField("bool")})
    bad_schema("field spec wrong type", "s", {"a": "bool"})
    bad_schema("text kind", "s", {"a": PayloadField("text")})
    bad_schema("id kind (canary-capable)", "s", {"a": PayloadField("id")})
    bad_schema("required non-bool", "s", {"a": PayloadField("bool", required=1)})  # type: ignore[arg-type]
    bad_schema("enum without values", "s", {"a": PayloadField("enum")})
    bad_schema(
        "enum 65 values", "s", {"a": PayloadField("enum", values=tuple(f"v{i}" for i in range(65)))}
    )
    bad_schema("enum duplicate", "s", {"a": PayloadField("enum", values=("x", "x"))})
    bad_schema("enum value malformed", "s", {"a": PayloadField("enum", values=("X Y",))})
    bad_schema("enum values list", "s", {"a": PayloadField("enum", values=["x"])})  # type: ignore[arg-type]
    bad_schema("values on non-enum", "s", {"a": PayloadField("bool", values=("x",))})
    check(
        "FTF-H027-P",
        "32 fields and 64 enum values accepted",
        not _raises(
            Exception, PayloadSchema, "s", {f"f{i}": PayloadField("bool") for i in range(32)}
        )
        and not _raises(
            Exception,
            PayloadSchema,
            "s",
            {"a": PayloadField("enum", values=tuple(f"v{i}" for i in range(64)))},
        ),
    )
    mutable = {"a": PayloadField("bool")}
    schema = PayloadSchema("s", mutable)
    mutable["b"] = PayloadField("bool")
    check("FTF-H027-P", "schema fields are frozen at construction", set(schema.fields) == {"a"})


def _catch(fn, *args: object) -> BaseException | None:
    try:
        fn(*args)
    except BaseException as exc:  # noqa: BLE001
        return exc
    return None


class _Coded(Exception):
    code = "dataset_quality_not_clear"


class _BadCode(Exception):
    code = "Has Spaces"


def test_failure_and_identity() -> None:
    cid = "0b0c1d2e-3f40-4152-8a63-748596a7b8c9"
    marker = generate_canary()
    out = sanitize_workflow_failure(_Coded(f"row 7 contained {marker.value}"), correlation_id=cid)
    check(
        "FTF-H027-W",
        "failure keeps only code and correlation id",
        out == b'{"code":"dataset_quality_not_clear","correlation_id":"' + cid.encode() + b'"}',
    )
    for err, why in (
        (ValueError(marker.value), "no code attribute"),
        (_BadCode(marker.value), "malformed class code"),
    ):
        got = sanitize_workflow_failure(err, correlation_id=cid)
        check(
            "FTF-H027-W",
            f"falls back to internal_error: {why}",
            json.loads(got)["code"] == "internal_error" and marker.value.encode() not in got,
        )
    inst = ValueError("x")
    inst.code = "leaked_instance_value"  # type: ignore[attr-defined]
    check(
        "FTF-H027-W",
        "an instance-level code is ignored",
        json.loads(sanitize_workflow_failure(inst, correlation_id=cid))["code"] == "internal_error",
    )
    chained = _Coded("outer")
    chained.__cause__ = RuntimeError(marker.value)
    check(
        "FTF-H027-W",
        "the cause chain is never serialised",
        marker.value.encode() not in sanitize_workflow_failure(chained, correlation_id=cid),
    )
    check(
        "FTF-H027-W",
        "malformed correlation id refused",
        _raises(
            InvalidWorkflowIdentityError, sanitize_workflow_failure, _Coded(), correlation_id="x"
        ),
    )
    check(
        "FTF-H027-W",
        "non-exception refused",
        _raises(PayloadRefusedError, sanitize_workflow_failure, "boom", correlation_id=cid),
    )
    queues = frozenset({"training", "data-prep"})
    check(
        "FTF-H027-W",
        "valid identity accepted",
        validate_workflow_identity(f"train_job-{cid}", "training", queues)
        == (f"train_job-{cid}", "training"),
    )
    for wid, queue, allowed, why in (
        (f"train-job-{cid}", "training", queues, "hyphenated kind"),
        (f"train_job-{cid}\n", "training", queues, "trailing newline"),
        (f"Train-{cid}", "training", queues, "uppercase kind"),
        (f"{marker.value}-{cid}", "training", queues, "canary as kind"),
        ("train_job-not-a-uuid", "training", queues, "no uuid"),
        (f"train_job-{cid}", "other", queues, "undeclared queue"),
        (f"train_job-{cid}", marker.value, queues, "canary queue"),
        (f"train_job-{cid}", "training", frozenset(), "no declared queues"),
        (f"train_job-{cid}", "training", {"training"}, "queues not a frozenset"),
        (f"train_job-{cid}", "training", frozenset({"Bad Queue"}), "malformed declared queue"),
        (5, "training", queues, "non-string workflow id"),
    ):
        check(
            "FTF-H027-W",
            f"identity refused: {why}",
            _raises(InvalidWorkflowIdentityError, validate_workflow_identity, wid, queue, allowed),
        )


# --- fault injection (Law 1) ----------------------------------------------

BOUNDARY_MODULE = ROOT / "packages" / "security" / "privacy_boundary.py"
PAYLOAD_MODULE = ROOT / "packages" / "security" / "workflow_payload_policy.py"

FAULTS: list[tuple[Path, str, str, str]] = [
    (
        BOUNDARY_MODULE,
        "endpoint not bound to its surface (any surface's endpoint is internal)",
        r"for endpoint in checked\.surfaces\[name\]:",
        "for endpoint in [e for s in checked.surfaces.values() for e in s]:",
    ),
    (
        BOUNDARY_MODULE,
        "boundary profile floor dropped",
        r"floor = resolve_effective_security_profile\(boundary\.profile, organization_profile\)",
        "floor = organization_profile",
    ),
    (
        BOUNDARY_MODULE,
        "authorized external route performed",
        r"    if classification\.endpoint is None:\n"
        r"        raise DestinationOutsideBoundaryError\(\n",
        "    if False:\n        raise DestinationOutsideBoundaryError(\n",
    ),
    (
        BOUNDARY_MODULE,
        "rebinding containment dropped",
        r"if address not in endpoint\.network:",
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "raw telemetry accepted",
        r'if name == "telemetry" and not allow_telemetry:',
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "public network accepted",
        r"    if not any\(\n        network\.version",
        "    if False and not any(\n        network.version",
    ),
    (
        BOUNDARY_MODULE,
        "metadata hostname accepted",
        r"if value in _CLOUD_METADATA_HOSTNAMES:",
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "alternate host encoding classified internal",
        r"if host is None or port is None or netloc_host != host:",
        "if host is None or port is None:",
    ),
    (
        BOUNDARY_MODULE,
        "threat-model exclusions optional",
        r"if set\(exclusions\) != REQUIRED_THREAT_MODEL_EXCLUSIONS:",
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "digest drops the surfaces",
        r"            for surface in BOUNDARY_SURFACES\n        \},",
        "            for surface in ()\n        },",
    ),
    (
        BOUNDARY_MODULE,
        "altered boundary trusted",
        r"if boundary_digest\(boundary\) != boundary\.digest:",
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "host outside its network accepted",
        r"if literal not in network:",
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "oversized response truncated into success",
        r"if len\(raw\) > _MAX_RESPONSE_BYTES:",
        "if False:",
    ),
    (
        BOUNDARY_MODULE,
        "telemetry skips FTF-0903 validation",
        r"    event = emit_event\(kind, fields\)\n",
        "    from packages.security.content_free_telemetry import TelemetryEvent as _T\n"
        "    event = _T(kind, dict(fields))\n",
    ),
    (
        BOUNDARY_MODULE,
        "header line breaks accepted",
        r'if any\(c in key \+ item for c in "\\r\\n\\x00"\):',
        "if False:",
    ),
    (
        PAYLOAD_MODULE,
        "free-text kind accepted as uuid",
        r'"uuid": lambda spec, value: _pattern_value\(_UUID_RE, value\),',
        '"uuid": lambda spec, value: value,',
    ),
    (
        PAYLOAD_MODULE,
        "undeclared field accepted",
        r"if type\(key\) is not str or key not in schema\.fields:\n"
        r"            raise PayloadRefusedError",
        "if type(key) is not str:\n            raise PayloadRefusedError",
    ),
    (
        PAYLOAD_MODULE,
        "bool accepted as count",
        r"if type\(value\) is not int or not 0 <= value <= _MAX_COUNT:",
        "if not isinstance(value, int) or not 0 <= value <= _MAX_COUNT:",
    ),
    (
        PAYLOAD_MODULE,
        "failure message serialised",
        r'document = \{"code": code, "correlation_id": correlation\}',
        'document = {"code": code, "correlation_id": correlation, "message": str(error)}',
    ),
    (
        PAYLOAD_MODULE,
        "instance-level code trusted",
        r'code = getattr\(type\(error\), "code", None\)',
        'code = getattr(error, "code", None)',
    ),
    (
        PAYLOAD_MODULE,
        "canonical re-check on decode dropped",
        r"if encode_workflow_payload\(checked, values\) != data:",
        "if False:",
    ),
    (
        PAYLOAD_MODULE,
        "undeclared task queue accepted",
        r"if type\(task_queue\) is not str or task_queue not in allowed_task_queues:",
        "if type(task_queue) is not str:",
    ),
]


def run_faults() -> None:
    originals = {
        path: path.read_text(encoding="utf-8") for path in (BOUNDARY_MODULE, PAYLOAD_MODULE)
    }
    try:
        for path, description, pattern, replacement in FAULTS:
            mutated, count = re.subn(
                pattern, replacement.replace("\\", "\\\\"), originals[path], count=1
            )
            check("FTF-H027-FAULT", f"fault pattern matches once: {description}", count == 1)
            if count != 1:
                continue
            path.write_text(mutated, encoding="utf-8")
            try:
                proc = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve())],
                    env={**os.environ, "FTFH027_SUITE_FAULT": "1"},
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            finally:
                path.write_text(originals[path], encoding="utf-8")
            check(
                "FTF-H027-FAULT", f"fault turns the suite RED: {description}", proc.returncode != 0
            )
            print(f"  fault -> {'RED' if proc.returncode != 0 else 'GREEN (!)'}: {description}")
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
    for path, text in originals.items():
        check(
            "FTF-H027-FAULT",
            f"module restored byte-identical: {path.name}",
            path.read_text(encoding="utf-8") == text,
        )


def main() -> int:
    test_manifest()
    test_classification()
    test_fetch()
    test_telemetry()
    test_payload()
    test_failure_and_identity()
    if os.environ.get("FTFH027_SUITE_FAULT") != "1" and not FAILURES:
        run_faults()
    if FAILURES:
        print("\n".join(FAILURES))
        print(f"FTF-H027-P1: {len(FAILURES)} failure(s)")
        return 1
    print("FTF-H027-P1: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
