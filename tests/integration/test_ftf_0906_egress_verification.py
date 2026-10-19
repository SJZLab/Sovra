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
    CanaryLeakedError,
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

    policy = ConnectionPolicy(
        name="ftf-0906-test",
        allowed_schemes=frozenset({"http", "https"}),
        allowed_ports=frozenset({_PORT_A, _RELAY.port}),
        explicit_private_allowlist=(__import__("ipaddress").ip_network("127.0.0.1/32"),),
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

    # --- Flow A: allowed, plaintext HTTP -----------------------------------
    enforcement: list[EnforcementObservation] = []
    logs: list[ConnectionLogEntry] = []
    inspections: list[PlaintextInspection] = []
    captures: list[PacketCaptureObservation] = []

    try:
        resp_a = guarded_fetch(
            f"http://127.0.0.1:{_PORT_A}/payload",
            policy,
            method="POST",
            headers=canary_headers,
            body=body,
        )
        a_connected = resp_a.status == 200
    except (OutboundGuardError, OutboundPolicyError):
        a_connected = False
    check("FTF-0906-ACC1", "flow A (allowed, plaintext) really connects", a_connected)
    enforcement.append(EnforcementObservation("A", "process_guard", "allow", "ok"))
    logs.append(ConnectionLogEntry("A", "client", "connected" if a_connected else "refused"))
    a_dest_arrived = len(_SINK_A_STATE.requests) >= 1
    logs.append(
        ConnectionLogEntry("A", "destination", "connected" if a_dest_arrived else "not_seen")
    )
    a_received_headers = _SINK_A_STATE.requests[-1]["headers"] if a_dest_arrived else {}
    a_literal_canary_arrived = (
        a_dest_arrived
        and isinstance(a_received_headers, dict)
        and a_received_headers.get("X-Canary-Literal") == marker.value
    )
    check(
        "FTF-0906-ACC1",
        "sink A really received the canary headers (positive control, real bytes)",
        a_literal_canary_arrived,
    )
    a_frag_reassembled = (
        isinstance(a_received_headers, dict)
        and a_received_headers.get("X-Canary-Frag-1", "")
        + a_received_headers.get("X-Canary-Frag-2", "")
        == marker.value
    )
    check(
        "FTF-0906-ACC1",
        "a canary fragmented across two real headers reassembles to the exact value "
        "once sink A actually received both halves",
        a_frag_reassembled,
    )
    # canary_found reflects the real positive control actually observed at
    # sink A, not a hard-coded claim (VACUOUS-POSITIVE-CONTROL).
    inspections.append(
        PlaintextInspection(
            "controlled_destination",
            "sink-A",
            "A",
            True,
            decrypted=False,
            canary_found=a_literal_canary_arrived,
        )
    )

    # --- Flow B: allowed, TLS through the recording relay ------------------
    try:
        resp_b = guarded_fetch(
            f"https://127.0.0.1:{_RELAY.port}/payload",
            policy,
            method="POST",
            headers=canary_headers,
            body=body,
        )
        b_connected = resp_b.status == 200
    except (OutboundGuardError, OutboundPolicyError):
        b_connected = False
    check("FTF-0906-ACC1", "flow B (allowed, TLS via relay) really connects", b_connected)
    enforcement.append(EnforcementObservation("B", "process_guard", "allow", "ok"))
    logs.append(ConnectionLogEntry("B", "client", "connected" if b_connected else "refused"))
    b_dest_arrived = len(_SINK_B_STATE.requests) >= 1
    logs.append(
        ConnectionLogEntry("B", "destination", "connected" if b_dest_arrived else "not_seen")
    )
    b_received_headers = _SINK_B_STATE.requests[-1]["headers"] if b_dest_arrived else {}
    b_literal_canary_arrived = (
        b_dest_arrived
        and isinstance(b_received_headers, dict)
        and b_received_headers.get("X-Canary-Literal") == marker.value
    )
    check(
        "FTF-0906-ACC1",
        "sink B really received the canary headers through the TLS relay (positive control)",
        b_literal_canary_arrived,
    )
    inspections.append(
        PlaintextInspection(
            "controlled_destination",
            "sink-B",
            "B",
            True,
            decrypted=True,
            canary_found=b_literal_canary_arrived,
        )
    )
    capture_text_has_plaintext = marker.value.encode() in bytes(_RELAY.capture)
    check(
        "FTF-0906-NEG1",
        "the relay's own capture is genuine ciphertext (the canary never appears in it raw)",
        not capture_text_has_plaintext and len(_RELAY.capture) > 0,
    )
    captures.append(PacketCaptureObservation("B", encrypted=True, canary_found=False))

    # --- Flow C: denied (destination exists but is not allowlisted) --------
    c_denial_code = "ERROR: not denied"
    try:
        guarded_fetch(
            f"http://127.0.0.2:{_PORT_C}/payload",
            policy,
            method="POST",
            headers=canary_headers,
            body=body,
        )
        c_denied = False
    except (OutboundGuardError, OutboundPolicyError) as exc:
        c_denied = True
        # UNOBSERVABLE-AS-NOT-SEEN: take the enforcement decision/code from
        # what the real guard actually raised, never a hard-coded guess.
        c_denial_code = str(exc)[:200]
    check("FTF-0906-NEG1", "flow C (not allowlisted) is really denied by the guard", c_denied)
    enforcement.append(EnforcementObservation("C", "process_guard", "deny", c_denial_code))
    logs.append(ConnectionLogEntry("C", "client", "refused" if c_denied else "connected"))
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
    d_denial_code = "ERROR: not denied"
    try:
        guarded_fetch(
            "http://169.254.169.254/latest/meta-data/",
            policy,
            method="GET",
        )
        d_denied = False
    except (OutboundGuardError, OutboundPolicyError) as exc:
        d_denied = True
        d_denial_code = str(exc)[:200]
    check("FTF-0906-NEG1", "flow D (cloud metadata) is really denied before any dial", d_denied)
    enforcement.append(EnforcementObservation("D", "process_guard", "deny", d_denial_code))
    logs.append(ConnectionLogEntry("D", "client", "refused" if d_denied else "connected"))
    # UNOBSERVABLE-AS-NOT-SEEN (Law 17): 169.254.169.254 is a real
    # reserved address this sandbox does not own and cannot run a
    # listener on, so its destination-side state was never actually
    # checked -- "unobserved" says so rather than fabricating the
    # convenient "not_seen" this test never verified.
    logs.append(ConnectionLogEntry("D", "destination", "unobserved"))

    # Snapshot the real access-log content *before* the error-path demo
    # below reuses (and deliberately dirties) the same shared buffer --
    # FABRICATED-INSPECTION: the real application_log inspection further
    # down scans this exact text instead of a hard-coded "clean" claim.
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
    clean_ledger = CanaryPlacementLedger({("error_path", "literal")})
    clean_placement = PlacementRecord(marker.canary_id, "error_path", "literal", "integration-test")
    clean_ledger.record_placement(clean_placement)
    clean_found = scan_store_for_canaries(
        clean_error_text.encode(), {marker.canary_id: marker}, (clean_placement,), clean_ledger
    )
    check(
        "FTF-0906-ACC1",
        "a careful error path really stays clean of the canary",
        clean_found == frozenset(),
    )
    # FABRICATED-INSPECTION: assert_leak_test_complete proves every placed
    # (canary, sink, encoding) combination this ledger declared was
    # really checked, not just that scan_store_for_canaries happened to
    # run once without raising.
    try:
        clean_ledger.assert_leak_test_complete()
        clean_ledger_complete = True
    except Exception:
        clean_ledger_complete = False
    check(
        "FTF-0906-ACC1",
        "the error_path ledger's own declared coverage is really complete",
        clean_ledger_complete,
    )
    inspections.append(
        PlaintextInspection(
            "error_path", "client-log", None, True, decrypted=False, canary_found=False
        )
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

    # ERROR-PATH-LEAK-NOT-IN-REPORT: the real leak this test just caught
    # standalone must also be flagged by verify_egress itself, as a real
    # canary_found inspection fed into a report -- not only by a bare
    # scan_store_for_canaries assertion disconnected from any report.
    # Modeled on its own small plan/evidence (never on the main A/B/C/D
    # evidence above, which stays leak-free): the request to sink A was
    # itself a permitted flow, and the bug is that its own failure path
    # (error_path -- never one of this flow's authorized sinks) leaked
    # the canary that should only ever have reached controlled_destination.
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
            EnforcementObservation("leak-demo", "process_guard", "allow", "ok"),
            EnforcementObservation("leak-demo-control", "process_guard", "deny", c_denial_code),
        ),
        connection_logs=(
            ConnectionLogEntry("leak-demo", "client", "connected"),
            ConnectionLogEntry("leak-demo", "destination", "connected"),
            ConnectionLogEntry("leak-demo-control", "client", "refused"),
            ConnectionLogEntry("leak-demo-control", "destination", "not_seen"),
        ),
        plaintext_inspections=(
            PlaintextInspection(
                "controlled_destination",
                "sink-A",
                "leak-demo",
                True,
                decrypted=False,
                canary_found=True,
            ),
            PlaintextInspection(
                "error_path", "buggy-log", "leak-demo", True, decrypted=False, canary_found=True
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
    # snapshotted before the error-path demo above reused the buffer --
    # never a hard-coded "clean" claim (FABRICATED-INSPECTION).
    access_log_ledger = CanaryPlacementLedger({("trace", "literal")})
    access_log_placement = PlacementRecord(marker.canary_id, "trace", "literal", "integration-test")
    access_log_ledger.record_placement(access_log_placement)
    access_log_found = scan_store_for_canaries(
        real_access_log_text.encode(),
        {marker.canary_id: marker},
        (access_log_placement,),
        access_log_ledger,
    )
    check(
        "FTF-0906-ACC1",
        "the real access-log text (method/path/status only, no headers) really stays clean",
        access_log_found == frozenset(),
    )
    inspections.append(
        PlaintextInspection(
            "application_log", "client-log", None, True, decrypted=False, canary_found=False
        )
    )

    # Real artifact inspection: the response bytes A/B actually returned,
    # saved to a real file on disk, genuinely never echo the canary.
    artifact_path = _TLS_TMP / "response-artifact.bin"
    artifact_path.write_bytes(resp_a.body if a_connected else b"")
    artifact_ledger = CanaryPlacementLedger({("artifact", "literal")})
    artifact_placement = PlacementRecord(
        marker.canary_id, "artifact", "literal", "integration-test"
    )
    artifact_ledger.record_placement(artifact_placement)
    artifact_found = scan_store_for_canaries(
        artifact_path.read_bytes(),
        {marker.canary_id: marker},
        (artifact_placement,),
        artifact_ledger,
    )
    check(
        "FTF-0906-ACC1",
        "the real saved artifact never echoes the canary",
        artifact_found == frozenset(),
    )
    try:
        artifact_ledger.assert_leak_test_complete()
        artifact_ledger_complete = True
    except Exception:
        artifact_ledger_complete = False
    check(
        "FTF-0906-ACC1",
        "the artifact ledger's own declared coverage is really complete",
        artifact_ledger_complete,
    )
    inspections.append(
        PlaintextInspection("artifact", str(artifact_path), None, True, decrypted=False)
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
    bypass_dest_arrived = len(_SINK_C_STATE.requests) >= 2  # the earlier guarded attempt + this one
    bypass_evidence = EgressEvidence(
        enforcement=(
            EnforcementObservation("bypass", "process_guard", "deny", "not_in_allowlist"),
            EnforcementObservation("control", "process_guard", "allow", "ok"),
        ),
        connection_logs=(
            # The guard was never consulted for this raw socket -- from the
            # verifier's point of view the client-side signal is exactly
            # what a real bypass looks like: nothing refused it.
            ConnectionLogEntry("bypass", "client", "connected"),
            ConnectionLogEntry(
                "bypass", "destination", "connected" if bypass_dest_arrived else "not_seen"
            ),
            ConnectionLogEntry("control", "client", "connected" if a_connected else "refused"),
            ConnectionLogEntry(
                "control", "destination", "connected" if a_dest_arrived else "not_seen"
            ),
        ),
        plaintext_inspections=(
            PlaintextInspection(
                "controlled_destination", "sink-A", "control", True, decrypted=False
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
