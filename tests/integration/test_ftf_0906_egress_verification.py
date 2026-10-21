#!/usr/bin/env python3
"""FTF-0906 -- Egress capture/verification (real-socket integration, no DB).

Same real local-TLS fixture convention every sibling FTF-H005/FTF-0203
suite already established (a self-signed CA generated and installed into
this sandbox's own system trust store for the run, removed again in
`atexit`) -- reused here rather than reinvented, so `packages.security.
outbound_guard.guarded_fetch`'s own unmodified `ssl.create_default_context()`
genuinely validates it.

Proves this task's own acceptance clause over real sockets (no mocks, no
DB, loopback only):

- (A) a plaintext HTTP sink on 127.0.0.1 and (B) a TLS sink on 127.0.0.1,
  BOTH inside this policy's explicit allowlist, are reached through the
  real FTF-H006 `guarded_fetch` with canaries placed literally and in
  every FTF-0905 encoding plus fragmented across two headers. B sits
  behind a real byte-for-byte recording TCP relay: the relay never
  terminates TLS, so its captured stream is genuine ciphertext -- the one
  real proof this task's own negative_acceptance requires ("no TLS packet
  capture alone shows absence of leakage").
- (C) a second real HTTP sink on 127.0.0.2, deliberately NOT in the
  allowlist, and (D) the AWS/GCP/Azure metadata address are both denied
  by the real guard before any byte reaches them -- proven by the
  destination sinks' own real "nothing arrived" state, not merely by the
  client-side exception.
- A real bypass (`socket.create_connection` straight to C, skipping the
  guard entirely) really lands and really carries its canary -- proving
  `verify_egress` would catch a genuine enforcement failure, not only a
  synthetic one.
- A real `error_path` leak (a developer mistake: logging full request
  context, canary included, on failure) is caught by `scan_store_for_
  canaries` even though FTF-0904's own redaction never recognizes a
  canary's shape -- disclosed in the module docstring as two independent
  mechanisms, not one subsuming the other.

Disclosed scope (same boundary `docs/implementation/task-packets/
FTF-0906.md` and the QUEUE.md design both name): `workflow_history` is
never exercised here -- no Temporal workflow exists in this repository
yet. Flow D's own destination (169.254.169.254, a real reserved address
this sandbox does not own) is denied before any dial, but its
destination-side state is never actually observed either -- reported as
`unobserved`, never a fabricated `not_seen` (Law 17). Both together mean
the final report is expected `incomplete`, never `passed`; every flow
this sandbox can actually fully observe (A, B, C) still verifies
`passed`. Process scope only; CNI/cluster-topology evidence is
FTF-H027's job on top of this one.

Run directly: python3 tests/integration/test_ftf_0906_egress_verification.py
"""

from __future__ import annotations

import atexit
import ipaddress
import logging
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.canary_secret_generator import (  # noqa: E402
    ENCODINGS,
    CanaryLeakedError,
    CanaryMarker,
    CanaryPlacementLedger,
    PlacementRecord,
    encode_canary,
    fragment_canary,
    generate_canary,
)
from packages.security.egress_capture_verification import (  # noqa: E402
    ConnectionLogEntry,
    EgressEvidence,
    EgressPlan,
    EnforcementObservation,
    PacketCaptureObservation,
    PlaintextInspection,
    PlannedFlow,
    scan_store_for_canaries,
    verify_egress,
)
from packages.security.log_redaction import attach_sink, install_redaction  # noqa: E402
from packages.security.outbound_guard import (  # noqa: E402
    GuardedResponse,
    OutboundGuardError,
    guarded_fetch,
)
from packages.security.url_policy import ConnectionPolicy, OutboundPolicyError  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# --- Real local TLS fixture (same convention as test_ftf_h005_route_attestation.py) --

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0906-tls-"))
# This directory holds the ephemeral CA's own private key -- never leave
# it behind on disk once the run ends (the directory itself, not just the
# CA trust-store registration `_remove_ca_trust` below already handles).
atexit.register(shutil.rmtree, _TLS_TMP, ignore_errors=True)
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0906-test-ca.crt")
_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key, ca_crt = _TLS_TMP / "ca.key", _TLS_TMP / "ca.crt"
    leaf_key, leaf_csr = _TLS_TMP / "leaf.key", _TLS_TMP / "leaf.csr"
    leaf_crt, leaf_ext = _TLS_TMP / "leaf.crt", _TLS_TMP / "leaf.ext"
    _run_openssl(["genrsa", "-out", str(ca_key), "2048"])
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "2",
            "-out",
            str(ca_crt),
            "-subj",
            "/CN=FTF-0906 Test CA",
            # Python 3.13's default verification context is strict: a CA
            # without basicConstraints/keyUsage is refused outright.
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        ]
    )
    _run_openssl(["genrsa", "-out", str(leaf_key), "2048"])
    _run_openssl(
        ["req", "-new", "-key", str(leaf_key), "-out", str(leaf_csr), "-subj", "/CN=127.0.0.1"]
    )
    leaf_ext.write_text("subjectAltName=IP:127.0.0.1\n")
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(leaf_crt),
            "-days",
            "2",
            "-sha256",
            "-extfile",
            str(leaf_ext),
        ]
    )
    subprocess.run(
        ["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)], check=True, capture_output=True
    )
    subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    atexit.register(_remove_ca_trust)
    return leaf_crt, leaf_key


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


_TRUSTED_LEAF: Path | None = None
_TRUSTED_KEY: Path | None = None
if _CA_AVAILABLE:
    _TRUSTED_LEAF, _TRUSTED_KEY = _generate_trusted_server_cert()
else:
    print(
        "FTF-0906: this sandbox has no openssl/update-ca-certificates/passwordless sudo -- "
        "the live-TLS-backed checks are skipped and disclosed here; GitHub Actions' "
        "ubuntu-latest runner has all three."
    )


# --- Shared redacted logger (the real FTF-0904 install_redaction/attach_sink wiring) --

install_redaction()
_LOG_BUFFER = StringIO()
_logger = logging.getLogger("ftf0906.egress-test")
_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(_LOG_BUFFER)
attach_sink(_handler)
_logger.addHandler(_handler)
_logger.propagate = False


# --- Real sinks --------------------------------------------------------------


class _RecordingState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, object]] = []

    def record(self, method: str, path: str, headers: dict[str, str], body: bytes) -> None:
        with self.lock:
            self.requests.append({"method": method, "path": path, "headers": headers, "body": body})


_SINK_A_STATE = _RecordingState()
_SINK_B_STATE = _RecordingState()
_SINK_C_STATE = _RecordingState()


def _make_handler(state: _RecordingState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            state.record(self.command, self.path, dict(self.headers.items()), body)
            # Access-log line: method/path/status only -- never raw header
            # values -- the same shape FTF-0904-W1's own real wiring logs.
            _logger.info("sink request method=%s path=%s status=200", self.command, self.path)
            response = b'{"received":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

    return _Handler


_SERVER_A = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_SINK_A_STATE))
_PORT_A = _SERVER_A.server_address[1]
threading.Thread(target=_SERVER_A.serve_forever, daemon=True).start()
atexit.register(_SERVER_A.shutdown)

_SERVER_C = ThreadingHTTPServer(("127.0.0.2", 0), _make_handler(_SINK_C_STATE))
_PORT_C = _SERVER_C.server_address[1]
threading.Thread(target=_SERVER_C.serve_forever, daemon=True).start()
atexit.register(_SERVER_C.shutdown)

_SERVER_B: ThreadingHTTPServer | None = None
_PORT_B_BACKEND: int | None = None
if _TRUSTED_LEAF is not None and _TRUSTED_KEY is not None:
    _SERVER_B = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_SINK_B_STATE))
    _tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    _tls_context.load_cert_chain(certfile=str(_TRUSTED_LEAF), keyfile=str(_TRUSTED_KEY))
    _SERVER_B.socket = _tls_context.wrap_socket(_SERVER_B.socket, server_side=True)
    _PORT_B_BACKEND = _SERVER_B.server_address[1]
    threading.Thread(target=_SERVER_B.serve_forever, daemon=True).start()
    atexit.register(_SERVER_B.shutdown)


class _RecordingRelay:
    """A transparent byte-for-byte TCP proxy: it never terminates TLS, so
    everything it captures is genuine ciphertext -- the real "packet
    capture" this task's own negative_acceptance names."""

    def __init__(self, backend_port: int) -> None:
        self.capture = bytearray()
        self._lock = threading.Lock()
        self._backend_port = backend_port
        self._stop = False
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(5)
        self.port = self._server_sock.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        self._server_sock.settimeout(0.5)
        while not self._stop:
            try:
                client_sock, _ = self._server_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client_sock,), daemon=True).start()

    def _handle(self, client_sock: socket.socket) -> None:
        try:
            backend_sock = socket.create_connection(("127.0.0.1", self._backend_port), timeout=5)
        except OSError:
            client_sock.close()
            return
        t1 = threading.Thread(target=self._pipe, args=(client_sock, backend_sock), daemon=True)
        t2 = threading.Thread(target=self._pipe, args=(backend_sock, client_sock), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def _pipe(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(4096)
                if not data:
                    break
                with self._lock:
                    self.capture.extend(data)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def stop(self) -> None:
        self._stop = True
        try:
            self._server_sock.close()
        except OSError:
            pass


_RELAY: _RecordingRelay | None = None
if _PORT_B_BACKEND is not None:
    _RELAY = _RecordingRelay(_PORT_B_BACKEND)
    atexit.register(_RELAY.stop)


def _request_bytes(request: dict[str, object]) -> bytes:
    """The exact bytes a sink really received: request line, every real
    header and the body -- what a downstream log scraper or packet
    inspector at the controlled destination would actually see."""
    headers = request["headers"]
    assert isinstance(headers, dict)
    lines = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
    body = request["body"]
    assert isinstance(body, bytes)
    return f"{request['method']} {request['path']} HTTP/1.1\r\n{lines}\r\n".encode() + body


def _scan(data: bytes, marker: CanaryMarker, sink: str, encodings: tuple[str, ...]) -> bool:
    """A REAL scan of `data` (never a typed-in claim): True when the canary
    is recovered in any declared encoding (the ledger's own fail-closed
    CanaryLeakedError is the positive result)."""
    ledger = CanaryPlacementLedger({(sink, encoding) for encoding in encodings})
    placements = []
    for encoding in encodings:
        placement = PlacementRecord(marker.canary_id, sink, encoding, "integration-test")
        ledger.record_placement(placement)
        placements.append(placement)
    try:
        found = scan_store_for_canaries(data, {marker.canary_id: marker}, tuple(placements), ledger)
    except CanaryLeakedError:
        return True
    return bool(found)


def _scan_store_clean(
    data: bytes, marker: CanaryMarker, sink: str, what: str
) -> PlaintextInspection:
    """Scan a store that must stay clean under EVERY FTF-0905 encoding and
    prove the ledger's declared coverage is complete (assert_leak_test_
    complete) -- the inspection's canary_found is the real scan result."""
    ledger = CanaryPlacementLedger({(sink, encoding) for encoding in ENCODINGS})
    placements = []
    for encoding in ENCODINGS:
        placement = PlacementRecord(marker.canary_id, sink, encoding, "integration-test")
        ledger.record_placement(placement)
        placements.append(placement)
    leaked = False
    try:
        found = scan_store_for_canaries(data, {marker.canary_id: marker}, tuple(placements), ledger)
        leaked = bool(found)
    except CanaryLeakedError:
        leaked = True
    try:
        ledger.assert_leak_test_complete()
        complete = True
    except Exception:
        complete = False
    check("FTF-0906-ACC1", f"the {what} stays clean of the canary under every encoding", not leaked)
    check(
        "FTF-0906-ACC1",
        f"the {what} ledger's declared coverage is really complete (assert_leak_test_complete)",
        complete,
    )
    store = {"trace": "application_log", "error_path": "error_path", "artifact": "artifact"}[sink]
    return PlaintextInspection(store, what, None, True, decrypted=False, canary_found=leaked)


def _guard_flow(
    url: str, policy: ConnectionPolicy, **kwargs: object
) -> tuple[GuardedResponse | None, str, str, str]:
    """Run one flow through the real guard and take the enforcement decision
    and client outcome from what the guard actually did: a policy refusal
    (OutboundPolicyError) is a deny; a connection failure AFTER the policy
    passed (OutboundGuardError) is NOT a deny -- the policy allowed it and
    the dial failed. Returns (response, decision, detail, client_outcome)."""
    try:
        response = guarded_fetch(url, policy, **kwargs)  # type: ignore[arg-type]
    except OutboundPolicyError as exc:
        return None, "deny", f"{type(exc).__name__}: {exc}", "refused"
    except OutboundGuardError as exc:
        return None, "allow", f"{type(exc).__name__}: {exc}", "reset"
    return response, "allow", "policy_allowed", "connected"


def main() -> None:
    if not _CA_AVAILABLE or _RELAY is None:
        # SKIP-EXIT-0: a missing openssl/update-ca-certificates/sudo means
        # this task's own real-TLS negative_acceptance clause ("no TLS
        # packet capture alone shows absence of leakage") was never
        # actually exercised -- that is a failure to prove the clause
        # (Law 17: an environment that cannot run the proof stays
        # visible as failed, never converted to a quiet success), not a
        # skip. GitHub Actions' ubuntu-latest runner has every required
        # tool, so this path is not expected to fire there.
        print(
            "FTF-0906 integration: openssl/update-ca-certificates/passwordless "
            "sudo unavailable in this sandbox -- the real-TLS negative_acceptance "
            "clause cannot be proven here. FAILING (not skipping): "
            "GitHub Actions' ubuntu-latest runner has every required tool."
        )
        sys.exit(1)

    # Ports 80 and _PORT_C are listed on purpose: flows C and D must be
    # refused by the rule they are meant to exercise (the explicit private
    # allowlist for C, the unconditional cloud-metadata rule for D), never
    # incidentally by the port rule that runs first. D's address is even
    # placed on the explicit allowlist: only the H006 metadata rule can
    # still deny it.
    policy = ConnectionPolicy(
        name="ftf-0906-test",
        allowed_schemes=frozenset({"http", "https"}),
        allowed_ports=frozenset({_PORT_A, _PORT_C, _RELAY.port, 80}),
        explicit_private_allowlist=(
            ipaddress.ip_network("127.0.0.1/32"),
            ipaddress.ip_network("169.254.169.254/32"),
        ),
    )

    marker = generate_canary()
    frag_a, frag_b = fragment_canary(marker, 2)
    canary_headers = {
        "X-Canary-Literal": marker.value,
        "X-Canary-B64": encode_canary(marker, "base64"),
        "X-Canary-Hex": encode_canary(marker, "hex"),
        "X-Canary-Url": encode_canary(marker, "url"),
        "X-Canary-Frag-1": frag_a,
        "X-Canary-Frag-2": frag_b,
    }
    body = b'{"payload":"ordinary, non-secret request body"}'

    enforcement: list[EnforcementObservation] = []
    logs: list[ConnectionLogEntry] = []
    inspections: list[PlaintextInspection] = []
    captures: list[PacketCaptureObservation] = []

    # --- Flow A: allowed, plaintext HTTP -----------------------------------
    resp_a, a_decision, a_detail, a_client = _guard_flow(
        f"http://127.0.0.1:{_PORT_A}/payload",
        policy,
        method="POST",
        headers=canary_headers,
        body=body,
    )
    a_connected = resp_a is not None and resp_a.status == 200
    check("FTF-0906-ACC1", "flow A (allowed, plaintext) really connects", a_connected)
    check("FTF-0906-ACC1", "flow A's enforcement decision is allow", a_decision == "allow")
    enforcement.append(EnforcementObservation("A", "process_guard", a_decision, a_detail[:200]))
    logs.append(ConnectionLogEntry("A", "client", a_client))
    a_dest_arrived = len(_SINK_A_STATE.requests) >= 1
    logs.append(
        ConnectionLogEntry("A", "destination", "connected" if a_dest_arrived else "not_seen")
    )
    a_request = _SINK_A_STATE.requests[-1] if a_dest_arrived else None
    a_request_bytes = _request_bytes(a_request) if a_request is not None else b""
    # The positive control is a REAL scan of the bytes sink A actually
    # received, never a typed-in True (VACUOUS-POSITIVE-CONTROL).
    a_canary_found = _scan(a_request_bytes, marker, "tool_result", ENCODINGS)
    check(
        "FTF-0906-ACC1",
        "sink A really received the canary (positive control: a real scan of its real bytes)",
        a_canary_found,
    )
    for encoding in ENCODINGS:
        check(
            "FTF-0906-ACC1",
            f"sink A's real request bytes carry the canary in encoding {encoding!r}",
            _scan(a_request_bytes, marker, "tool_result", (encoding,)),
        )
    inspections.append(
        PlaintextInspection(
            "controlled_destination",
            "sink-A",
            "A",
            True,
            decrypted=False,
            canary_found=a_canary_found,
        )
    )

    # FRAGMENT-SCANNER-GAP: a SECOND real request to sink A carries ONLY the
    # two fragment headers -- the contiguous literal is nowhere in the bytes
    # sink A received, so only a scanner that finds pieces landing in
    # separate headers can see it.
    frag_only_headers = {"X-Canary-Frag-1": frag_a, "X-Canary-Frag-2": frag_b}
    frag_resp, _, _, _ = _guard_flow(
        f"http://127.0.0.1:{_PORT_A}/fragments",
        policy,
        method="POST",
        headers=frag_only_headers,
        body=body,
    )
    check("FTF-0906-ACC1", "the fragment-only request really reaches sink A", frag_resp is not None)
    frag_request = _SINK_A_STATE.requests[-1]
    frag_bytes = _request_bytes(frag_request)
    frag_header_names = [str(name).lower() for name in frag_request["headers"]]  # type: ignore[attr-defined]
    check(
        "FTF-0906-ACC1",
        "sink A really received both fragments as two separate headers",
        "x-canary-frag-1" in frag_header_names and "x-canary-frag-2" in frag_header_names,
    )
    check(
        "FTF-0906-ACC1",
        "the contiguous literal is NOT in the bytes sink A received (pieces are separate)",
        marker.value.encode() not in frag_bytes,
    )
    check(
        "FTF-0906-ACC1",
        "the fragmented scan finds the canary scattered across the two real headers",
        _scan(frag_bytes, marker, "tool_result", ("fragmented",)),
    )
    check(
        "FTF-0906-ACC1",
        "a literal-only scan does not (the fragments are the only evidence)",
        not _scan(frag_bytes, marker, "tool_result", ("literal",)),
    )

    # --- Flow B: allowed, TLS through the recording relay ------------------
    resp_b, b_decision, b_detail, b_client = _guard_flow(
        f"https://127.0.0.1:{_RELAY.port}/payload",
        policy,
        method="POST",
        headers=canary_headers,
        body=body,
    )
    b_connected = resp_b is not None and resp_b.status == 200
    check("FTF-0906-ACC1", "flow B (allowed, TLS via relay) really connects", b_connected)
    check("FTF-0906-ACC1", "flow B's enforcement decision is allow", b_decision == "allow")
    enforcement.append(EnforcementObservation("B", "process_guard", b_decision, b_detail[:200]))
    logs.append(ConnectionLogEntry("B", "client", b_client))
    b_dest_arrived = len(_SINK_B_STATE.requests) >= 1
    logs.append(
        ConnectionLogEntry("B", "destination", "connected" if b_dest_arrived else "not_seen")
    )
    b_request_bytes = _request_bytes(_SINK_B_STATE.requests[-1]) if b_dest_arrived else b""
    b_canary_found = _scan(b_request_bytes, marker, "tool_result", ENCODINGS)
    check(
        "FTF-0906-ACC1",
        "sink B really received the canary through the TLS relay (positive control: real scan)",
        b_canary_found,
    )
    inspections.append(
        PlaintextInspection(
            "controlled_destination",
            "sink-B",
            "B",
            True,
            decrypted=True,
            canary_found=b_canary_found,
        )
    )
    relay_capture = bytes(_RELAY.capture)
    # The capture's canary_found is a REAL scan of the real ciphertext
    # (never a typed-in False): it genuinely cannot see the canary.
    capture_canary_found = _scan(relay_capture, marker, "tool_result", ENCODINGS)
    check(
        "FTF-0906-NEG1",
        "the relay's own capture is genuine ciphertext (the canary is in none of its encodings)",
        not capture_canary_found and len(relay_capture) > 0,
    )
    captures.append(
        PacketCaptureObservation("B", encrypted=True, canary_found=capture_canary_found)
    )

    # --- Flow C: denied (destination exists but is not allowlisted) --------
    resp_c, c_decision, c_detail, c_client = _guard_flow(
        f"http://127.0.0.2:{_PORT_C}/payload",
        policy,
        method="POST",
        headers=canary_headers,
        body=body,
    )
    check("FTF-0906-NEG1", "flow C (not allowlisted) is really denied by the guard", resp_c is None)
    check(
        "FTF-0906-NEG1",
        "flow C is a POLICY denial (OutboundPolicyError), not a connection failure",
        c_decision == "deny" and c_client == "refused",
    )
    check(
        "FTF-0906-NEG1",
        "flow C is refused by the explicit-allowlist rule, not incidentally by the port rule",
        "explicit_private_allowlist" in c_detail and "allowed_ports" not in c_detail,
    )
    enforcement.append(EnforcementObservation("C", "process_guard", c_decision, c_detail[:200]))
    logs.append(ConnectionLogEntry("C", "client", c_client))
    c_dest_arrived = len(_SINK_C_STATE.requests) >= 1
    check(
        "FTF-0906-NEG1",
        "sink C genuinely never received anything (not merely a client-side claim)",
        not c_dest_arrived,
    )
    logs.append(
        ConnectionLogEntry("C", "destination", "not_seen" if not c_dest_arrived else "connected")
    )

    # --- Flow D: denied (cloud metadata, unconditional) ---------------------
    resp_d, d_decision, d_detail, d_client = _guard_flow(
        "http://169.254.169.254/latest/meta-data/", policy, method="GET", timeout=3.0
    )
    check(
        "FTF-0906-NEG1", "flow D (cloud metadata) is really denied before any dial", resp_d is None
    )
    check(
        "FTF-0906-NEG1",
        "flow D is a POLICY denial (OutboundPolicyError), not a connection failure",
        d_decision == "deny" and d_client == "refused",
    )
    # G1: flow D must reach the H006 cloud-metadata rule itself -- not the
    # port rule (port 80 is allowed above) and not the allowlist rule (the
    # address is on the allowlist above). Removing the metadata rule must
    # turn this RED.
    check(
        "FTF-0906-NEG1",
        "flow D is refused by the H006 cloud-metadata rule specifically",
        "cloud-metadata destination" in d_detail
        and "allowed_ports" not in d_detail
        and "explicit_private_allowlist" not in d_detail,
    )
    enforcement.append(EnforcementObservation("D", "process_guard", d_decision, d_detail[:200]))
    logs.append(ConnectionLogEntry("D", "client", d_client))
    # UNOBSERVABLE-AS-NOT-SEEN (Law 17): 169.254.169.254 is a real
    # reserved address this sandbox does not own and cannot run a
    # listener on, so its destination-side state was never actually
    # checked -- "unobserved" says so rather than fabricating the
    # convenient "not_seen" this test never verified.
    logs.append(ConnectionLogEntry("D", "destination", "unobserved"))

    # Snapshot the real access-log content *before* the error-path demo
    # below reuses (and deliberately dirties) the same shared buffer --
    # the real application_log inspection further down scans this exact
    # text instead of a hard-coded "clean" claim.
    real_access_log_text = _LOG_BUFFER.getvalue()

    # --- Real error_path leak check: a developer mistake is really caught ---
    _LOG_BUFFER.truncate(0)
    _LOG_BUFFER.seek(0)
    try:
        raise RuntimeError("request context")
    except RuntimeError:
        # The clean path: never log full context on error.
        _logger.info("request to sink A failed, retrying (no context logged)")
    clean_error_text = _LOG_BUFFER.getvalue()
    inspections.append(
        _scan_store_clean(clean_error_text.encode(), marker, "error_path", "error-path log")
    )

    _LOG_BUFFER.truncate(0)
    _LOG_BUFFER.seek(0)
    try:
        raise RuntimeError(f"request to sink A failed; headers were {canary_headers!r}")
    except RuntimeError as exc:
        # The realistic bug: logging full context, canary included.
        _logger.info("request failed: %s", exc)
    buggy_error_text = _LOG_BUFFER.getvalue()
    buggy_ledger = CanaryPlacementLedger({("error_path", "literal")})
    buggy_placement = PlacementRecord(marker.canary_id, "error_path", "literal", "integration-test")
    buggy_ledger.record_placement(buggy_placement)
    leak_was_caught = False
    try:
        scan_store_for_canaries(
            buggy_error_text.encode(), {marker.canary_id: marker}, (buggy_placement,), buggy_ledger
        )
    except CanaryLeakedError:
        leak_was_caught = True
    check(
        "FTF-0906-FAULT",
        "a real developer mistake (full context logged on error) is really caught, "
        "even though FTF-0904 does not recognize a canary's shape",
        leak_was_caught and marker.value in buggy_error_text,
    )
    try:
        buggy_ledger.assert_leak_test_complete()
        buggy_ledger_refused = False
    except CanaryLeakedError:
        buggy_ledger_refused = True
    check(
        "FTF-0906-FAULT",
        "the leaking error-path ledger is never called complete (assert_leak_test_complete "
        "refuses it)",
        buggy_ledger_refused,
    )

    # ERROR-PATH-LEAK-NOT-IN-REPORT: the real leak this test just caught
    # standalone must also be flagged by verify_egress itself, as a real
    # canary_found inspection fed into a report -- not only by a bare
    # scan_store_for_canaries assertion disconnected from any report.
    # Every canary_found below is a real scan result (sink A's real bytes,
    # the real CanaryLeakedError just caught), never a typed-in True.
    leak_demo_plan = EgressPlan(
        plan_id="ftf-0906-error-path-leak-demo",
        scope="process",
        policy_digest="d" * 64,
        flows=(
            PlannedFlow("leak-demo", "data", "sink-A", "internal", "allow"),
            PlannedFlow(
                "leak-demo-control", "data", "sink-C (not allowlisted)", "external", "deny"
            ),
        ),
    )
    leak_demo_evidence = EgressEvidence(
        enforcement=(
            EnforcementObservation("leak-demo", "process_guard", a_decision, a_detail[:200]),
            EnforcementObservation(
                "leak-demo-control", "process_guard", c_decision, c_detail[:200]
            ),
        ),
        connection_logs=(
            ConnectionLogEntry("leak-demo", "client", a_client),
            ConnectionLogEntry("leak-demo", "destination", "connected"),
            ConnectionLogEntry("leak-demo-control", "client", c_client),
            ConnectionLogEntry("leak-demo-control", "destination", "not_seen"),
        ),
        plaintext_inspections=(
            PlaintextInspection(
                "controlled_destination",
                "sink-A",
                "leak-demo",
                True,
                decrypted=False,
                canary_found=a_canary_found,
            ),
            PlaintextInspection(
                "error_path",
                "buggy-log",
                "leak-demo",
                True,
                decrypted=False,
                canary_found=leak_was_caught,
            ),
        ),
    )
    leak_demo_report = verify_egress(leak_demo_plan, leak_demo_evidence)
    leak_demo_verdict = next(v for v in leak_demo_report.flow_verdicts if v.flow_id == "leak-demo")
    check(
        "FTF-0906-FAULT",
        "the real error-path leak this test actually caught is also flagged by "
        "verify_egress itself (report, not only a standalone scan)",
        leak_demo_verdict.status == "failed" and leak_demo_verdict.codes == ("canary_leaked",),
    )

    # Real application_log inspection: scan the real access-log text
    # snapshotted before the error-path demo above reused the buffer.
    check(
        "FTF-0906-ACC1",
        "the real access-log text carries real request lines (the scan is not vacuous)",
        "sink request method=POST" in real_access_log_text,
    )
    inspections.append(
        _scan_store_clean(real_access_log_text.encode(), marker, "trace", "access log")
    )

    # Real artifact inspection: the response bytes A actually returned,
    # saved to a real file on disk, genuinely never echo the canary.
    artifact_path = _TLS_TMP / "response-artifact.bin"
    artifact_path.write_bytes(resp_a.body if resp_a is not None else b"")
    inspections.append(
        _scan_store_clean(artifact_path.read_bytes(), marker, "artifact", "saved artifact")
    )

    plan = EgressPlan(
        plan_id="ftf-0906-integration",
        scope="process",
        policy_digest="b" * 64,
        flows=(
            PlannedFlow("A", "data", "sink-A", "internal", "allow"),
            PlannedFlow("B", "data", "sink-B", "internal", "allow"),
            PlannedFlow("C", "data", "sink-C (not allowlisted)", "external", "deny"),
            PlannedFlow("D", "data", "cloud-metadata", "metadata", "deny"),
        ),
    )
    evidence = EgressEvidence(
        enforcement=tuple(enforcement),
        connection_logs=tuple(logs),
        plaintext_inspections=tuple(inspections),
        packet_captures=tuple(captures),
    )
    report = verify_egress(plan, evidence)
    flow_verdicts_by_id = {v.flow_id: v for v in report.flow_verdicts}
    check(
        "FTF-0906-ACC1",
        "every real, observable flow verdict (A, B, C) is passed",
        all(flow_verdicts_by_id[fid].status == "passed" for fid in ("A", "B", "C")),
    )
    check(
        "FTF-0906-SCOPE",
        "flow D is incomplete, never passed or a specific failure claim, since this "
        "sandbox genuinely cannot observe the real cloud-metadata destination's own "
        "state (UNOBSERVABLE-AS-NOT-SEEN, Law 17)",
        flow_verdicts_by_id["D"].status == "incomplete"
        and flow_verdicts_by_id["D"].codes == ("destination_unobserved",),
    )
    check(
        "FTF-0906-SCOPE",
        "overall status is incomplete, never passed (workflow_history disclosed as never "
        "exercised, and flow D's own destination state genuinely never observed)",
        report.status == "incomplete" and report.not_exercised == ("workflow_history",),
    )

    # --- Real enforcement-bypass fault: skip the guard entirely -----------
    try:
        raw_sock = socket.create_connection(("127.0.0.2", _PORT_C), timeout=5)
        request = (
            f"POST /bypass HTTP/1.1\r\nHost: 127.0.0.2\r\n"
            f"X-Canary-Literal: {marker.value}\r\n"
            f"Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode()
        raw_sock.sendall(request)
        raw_sock.recv(4096)
        raw_sock.close()
        bypass_landed = True
    except OSError:
        bypass_landed = False
    check(
        "FTF-0906-FAULT",
        "a direct socket bypass around the guard really lands on C (proves the fixture itself, "
        "not merely the guard, can reach C)",
        bypass_landed,
    )
    bypass_plan = EgressPlan(
        plan_id="ftf-0906-bypass",
        scope="process",
        policy_digest="c" * 64,
        flows=(
            PlannedFlow("bypass", "data", "sink-C", "external", "deny"),
            PlannedFlow("control", "data", "sink-A", "internal", "allow"),
        ),
    )
    bypass_dest_arrived = len(_SINK_C_STATE.requests) >= 1
    bypass_canary_found = bypass_dest_arrived and _scan(
        _request_bytes(_SINK_C_STATE.requests[-1]), marker, "tool_result", ENCODINGS
    )
    bypass_evidence = EgressEvidence(
        enforcement=(
            EnforcementObservation("bypass", "process_guard", "deny", "not_in_allowlist"),
            EnforcementObservation("control", "process_guard", a_decision, a_detail[:200]),
        ),
        connection_logs=(
            # The guard was never consulted for this raw socket -- from the
            # verifier's point of view the client-side signal is exactly
            # what a real bypass looks like: nothing refused it.
            ConnectionLogEntry("bypass", "client", "connected" if bypass_landed else "refused"),
            ConnectionLogEntry(
                "bypass", "destination", "connected" if bypass_dest_arrived else "not_seen"
            ),
            ConnectionLogEntry("control", "client", a_client),
            ConnectionLogEntry(
                "control", "destination", "connected" if a_dest_arrived else "not_seen"
            ),
        ),
        plaintext_inspections=(
            PlaintextInspection(
                "controlled_destination",
                "sink-C",
                "bypass",
                True,
                decrypted=False,
                canary_found=bypass_canary_found,
            ),
            PlaintextInspection(
                "controlled_destination",
                "sink-A",
                "control",
                True,
                decrypted=False,
                canary_found=a_canary_found,
            ),
        ),
    )
    bypass_report = verify_egress(bypass_plan, bypass_evidence)
    bypass_verdict = next(v for v in bypass_report.flow_verdicts if v.flow_id == "bypass")
    check(
        "FTF-0906-FAULT",
        "the verifier catches a real enforcement bypass as failed/unauthorized_transfer",
        bypass_verdict.status == "failed" and bypass_verdict.codes == ("unauthorized_transfer",),
    )

    if FAILURES:
        print(f"FTF-0906 egress-verification integration: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0906 egress-verification integration: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
