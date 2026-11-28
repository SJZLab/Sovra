#!/usr/bin/env python3
"""FTF-H027-P1 -- measured process-scope egress evidence for the declared private boundary.

Every effect goes through `packages.security.privacy_boundary.boundary_fetch` /
`send_boundary_telemetry` against real sockets. Every verdict comes from
FTF-0906 `verify_egress`, fed only with what really happened: what the boundary
raised or returned, what each real sink recorded, and real scans of real bytes.

Topology (loopback on one GitHub Actions runner, process scope):
  A   data      http  127.0.0.1 (IP literal)                       allowed
  B   judge     https h027-judge.boundary.test -> 127.0.0.1 (DNS)  allowed; TLS sink records
                DECRYPTED bytes behind a byte-for-byte relay whose capture is the ciphertext
  V6  registry  http  h027-v6.boundary.test -> ::1 (DNS, IPv6)     allowed
  X   data      undeclared name -> 127.0.0.2, a listening sink     denied (FTF-0901, no DNS lookup)
  J   judge     external judge attempt, name -> 127.0.0.2          denied
  MAP data      [::ffff:127.0.0.2] IPv4-mapped literal             denied
  R   preview   declared name, DNS answer 127.0.0.3 is outside
                its declared 10.0.0.0/8 network (rebinding)        denied
                (resolved_outside_boundary)
  M4  data      100.100.100.200 (Alibaba metadata), real listener  denied
  M6  data      fd00:ec2::254 (AWS IMDSv6), real listener          denied
Every denied destination has a real listener, so "not_seen" is a checked absence.
169.254.169.254 is also refused, but it is not a 0906 flow: binding it would
hijack the runner VM's own metadata service, so its far side is never observed
(cluster-scope metadata evidence with a real listener is FTF-H027-P3).

Plus: content telemetry (FTF-0903 event reaches the declared collector; an event
or raw body carrying a canary is refused before any socket), the error path
(a refused, canary-bearing request logged through FTF-0904 redaction leaves no
canary in the log because boundary errors never echo input), the application
log and a saved response artifact, all scanned in every FTF-0905 encoding.

Expected result: `partial` with not_exercised=("workflow_history",). Real Temporal
history is FTF-H027-P2. Every flow verdict is `passed`. A real raw-socket bypass
and an encrypted-capture-only clearance are each shown RED through the same verifier.

Prerequisites (openssl, update-ca-certificates, passwordless sudo, `ip`, IPv6
loopback): a missing one FAILS this script. It never skips (Law 17).

Disclosed: process scope only. A worker that opens its own socket bypasses
in-process controls, and the bypass row proves that the verifier, not the
boundary, catches it. CNI scope is FTF-H027-P3. Passing means these tested
controls passed on this topology. It never means "no leakage is possible"
(§12): operator access, model memorization, privileged-host compromise and
ciphertext-only capture stay recorded as exclusions.
"""

from __future__ import annotations

import atexit
import hashlib
import json
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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.canary_secret_generator import (  # noqa: E402
    ENCODINGS,
    CanaryLeakedError,
    CanaryPlacementLedger,
    PlacementRecord,
    encode_canary,
    fragment_canary,
    generate_canary,
)
from packages.security.content_free_telemetry import ContentRefusedError  # noqa: E402
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
from packages.security.privacy_boundary import (  # noqa: E402
    BOUNDARY_SURFACES,
    SURFACE_TO_EFFECT_SURFACE,
    PrivacyBoundaryError,
    RawTelemetryRefusedError,
    boundary_fetch,
    parse_private_boundary,
    send_boundary_telemetry,
)
from packages.security.security_profile_enforcement import (  # noqa: E402
    SecurityProfileEnforcementError,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _fail_now(message: str) -> None:
    print(f"FTF-H027-P1 integration: {message} -- FAILING, not skipping (Law 17)")
    sys.exit(1)


def _sudo(*args: str) -> None:
    subprocess.run(["sudo", "-n", *args], check=True, capture_output=True)


for tool in ("openssl", "update-ca-certificates", "sudo", "ip"):
    if shutil.which(tool) is None:
        _fail_now(f"required tool {tool!r} is missing")
if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
    _fail_now("passwordless sudo is unavailable")

JUDGE_NAME = "h027-judge.boundary.test"
V6_NAME = "h027-v6.boundary.test"
UNDECLARED_NAME = "h027-undeclared.boundary.test"
EXTERNAL_JUDGE_NAME = "h027-external-judge.boundary.test"
REBIND_NAME = "h027-rebind.boundary.test"
META4 = "100.100.100.200"
META6 = "fd00:ec2::254"

# --- host setup: metadata listener addresses, DNS answers, test CA ---------

_TMP = Path(tempfile.mkdtemp(prefix="ftf-h027-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
_HOSTS_BACKUP = _TMP / "hosts.orig"
shutil.copyfile("/etc/hosts", _HOSTS_BACKUP)


def _restore_host() -> None:
    subprocess.run(["sudo", "-n", "cp", str(_HOSTS_BACKUP), "/etc/hosts"], capture_output=True)
    subprocess.run(
        ["sudo", "-n", "ip", "addr", "del", f"{META4}/32", "dev", "lo"], capture_output=True
    )
    subprocess.run(
        ["sudo", "-n", "ip", "-6", "addr", "del", f"{META6}/128", "dev", "lo"], capture_output=True
    )


atexit.register(_restore_host)
try:
    _sudo("ip", "addr", "add", f"{META4}/32", "dev", "lo")
    _sudo("ip", "-6", "addr", "add", f"{META6}/128", "dev", "lo", "nodad")
except subprocess.CalledProcessError as exc:
    _fail_now(f"cannot add metadata listener addresses: {exc.stderr!r}")
_hosts_extra = (
    f"127.0.0.1 {JUDGE_NAME}\n"
    f"::1 {V6_NAME}\n"
    f"127.0.0.2 {UNDECLARED_NAME}\n"
    f"127.0.0.2 {EXTERNAL_JUDGE_NAME}\n"
    f"127.0.0.3 {REBIND_NAME}\n"
)
_new_hosts = _TMP / "hosts.new"
_new_hosts.write_text(_HOSTS_BACKUP.read_text() + "\n" + _hosts_extra)
_sudo("cp", str(_new_hosts), "/etc/hosts")
for name, family in ((JUDGE_NAME, socket.AF_INET), (V6_NAME, socket.AF_INET6)):
    try:
        socket.getaddrinfo(name, 80, family, socket.SOCK_STREAM)
    except socket.gaierror:
        _fail_now(f"the resolver does not answer {name} from /etc/hosts")

_CA_TARGET = Path("/usr/local/share/ca-certificates/ftf-h027-test-ca.crt")


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _make_cert() -> tuple[Path, Path]:
    ca_key, ca_crt = _TMP / "ca.key", _TMP / "ca.crt"
    key, csr, crt, ext = _TMP / "leaf.key", _TMP / "leaf.csr", _TMP / "leaf.crt", _TMP / "leaf.ext"
    _openssl("genrsa", "-out", str(ca_key), "2048")
    _openssl(
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
        "/CN=FTF-H027 Test CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    _openssl("genrsa", "-out", str(key), "2048")
    _openssl("req", "-new", "-key", str(key), "-out", str(csr), "-subj", f"/CN={JUDGE_NAME}")
    ext.write_text(f"subjectAltName=DNS:{JUDGE_NAME}\n")
    _openssl(
        "x509",
        "-req",
        "-in",
        str(csr),
        "-CA",
        str(ca_crt),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-out",
        str(crt),
        "-days",
        "2",
        "-sha256",
        "-extfile",
        str(ext),
    )
    _sudo("cp", str(ca_crt), str(_CA_TARGET))
    _sudo("update-ca-certificates")
    atexit.register(
        lambda: (
            subprocess.run(["sudo", "-n", "rm", "-f", str(_CA_TARGET)], capture_output=True),
            subprocess.run(
                ["sudo", "-n", "update-ca-certificates", "--fresh"], capture_output=True
            ),
        )
    )
    return crt, key


LEAF_CRT, LEAF_KEY = _make_cert()

# --- logging through the real FTF-0904 redaction ---------------------------

install_redaction()
LOG = StringIO()
logger = logging.getLogger("ftfh027.client")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(LOG)
attach_sink(_handler)
logger.addHandler(_handler)
logger.propagate = False

# --- real sinks ---------------------------------------------------------------


class Sink:
    def __init__(
        self, label: str, host: str, family: int = socket.AF_INET, tls: ssl.SSLContext | None = None
    ) -> None:
        self.label = label
        self.requests: list[bytes] = []
        self.lock = threading.Lock()
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                raw = (self.requestline + "\r\n" + str(self.headers)).encode() + body
                with sink.lock:
                    sink.requests.append(raw)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            do_GET = _handle  # noqa: N815
            do_POST = _handle  # noqa: N815

            def log_message(self, *args: object) -> None:
                return

        class Server(ThreadingHTTPServer):
            address_family = family

        self.server = Server((host, 0), Handler)
        if tls is not None:
            self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        atexit.register(self.server.shutdown)

    def data(self) -> bytes:
        with self.lock:
            return b"\n".join(self.requests)


class Relay:
    """Byte-for-byte TCP relay in front of the TLS sink. It never terminates TLS,
    so its capture is genuine ciphertext (the "packet capture")."""

    def __init__(self, backend_port: int) -> None:
        self.capture = bytearray()
        self.lock = threading.Lock()
        self.backend_port = backend_port
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            backend = socket.create_connection(("127.0.0.1", self.backend_port), timeout=5)
            for src, dst in ((client, backend), (backend, client)):
                threading.Thread(target=self._pipe, args=(src, dst), daemon=True).start()

    def _pipe(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(4096):
                with self.lock:
                    self.capture.extend(data)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass


_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
_tls.load_cert_chain(certfile=str(LEAF_CRT), keyfile=str(LEAF_KEY))
SINK_A = Sink("sink-a", "127.0.0.1")
SINK_B = Sink("sink-b-tls", "127.0.0.1", tls=_tls)
RELAY_B = Relay(SINK_B.port)
try:
    SINK_V6 = Sink("sink-v6", "::1", socket.AF_INET6)
except OSError:
    _fail_now("IPv6 loopback ::1 cannot be bound")
SINK_X = Sink("sink-x-undeclared", "127.0.0.2")
SINK_R = Sink("sink-r-rebind", "127.0.0.3")
SINK_M4 = Sink("sink-m4-metadata", META4)
SINK_M6 = Sink("sink-m6-metadata", META6, socket.AF_INET6)
COLLECTOR = Sink("collector", "127.0.0.1")
# The rebinding sink listens on the port the declared endpoint names, so a
# missing containment check would really land there.
_dead = socket.socket()
_dead.bind(("127.0.0.1", 0))
DEAD_PORT = _dead.getsockname()[1]
_dead.close()


def _ep(name: str, scheme: str, host: str, port: int, network: str) -> dict:
    return {"name": name, "scheme": scheme, "host": host, "port": port, "network": network}


surfaces: dict[str, list] = {s: [] for s in BOUNDARY_SURFACES}
surfaces["data"] = [_ep("sink-a", "http", "127.0.0.1", SINK_A.port, "127.0.0.1/32")]
surfaces["judge"] = [_ep("judge", "https", JUDGE_NAME, RELAY_B.port, "127.0.0.1/32")]
surfaces["registry"] = [_ep("registry-v6", "http", V6_NAME, SINK_V6.port, "::1/128")]
surfaces["preview"] = [_ep("rebind", "http", REBIND_NAME, SINK_R.port, "10.0.0.0/8")]
surfaces["source"] = [_ep("dead", "http", "127.0.0.1", DEAD_PORT, "127.0.0.1/32")]
surfaces["telemetry"] = [_ep("collector", "http", "127.0.0.1", COLLECTOR.port, "127.0.0.1/32")]
BOUNDARY = parse_private_boundary(
    {
        "schema_version": 1,
        "boundary_id": "ftf-h027-p1-ci",
        "profile": "zero_egress",
        "deployment": {
            "account": "github-actions-runner",
            "cluster": "none",
            "network": "runner-loopback",
            "storage": "none",
            "region": "runner",
            "operator_identities": ["ci-operator"],
        },
        "threat_model_exclusions": [
            "authorized_operator_access",
            "model_memorization",
            "privileged_host_compromise",
            "ciphertext_only_capture",
        ],
        "surfaces": surfaces,
    }
)
PROFILES: dict[str, Any] = {"organization_profile": "standard", "project_profile": "standard"}

marker = generate_canary()
frag_1, frag_2 = fragment_canary(marker, 2)
CANARY_HEADERS = {
    "X-Canary-Literal": marker.value,
    "X-Canary-B64": encode_canary(marker, "base64"),
    "X-Canary-Hex": encode_canary(marker, "hex"),
    "X-Canary-Url": encode_canary(marker, "url"),
    "X-Canary-Frag-1": frag_1,
    "X-Canary-Frag-2": frag_2,
}


def scan(data: bytes, store_sink: str, encodings: tuple[str, ...] = ENCODINGS) -> tuple[bool, bool]:
    """(found, coverage_complete) from a real FTF-0905 ledger scan of real bytes."""
    ledger = CanaryPlacementLedger({(store_sink, e) for e in encodings})
    placements = []
    for encoding in encodings:
        placement = PlacementRecord(marker.canary_id, store_sink, encoding, "ftf-h027-p1")
        ledger.record_placement(placement)
        placements.append(placement)
    try:
        found = bool(scan_store_for_canaries(data, {marker.canary_id: marker}, placements, ledger))
    except CanaryLeakedError:
        return True, False
    try:
        ledger.assert_leak_test_complete()
        complete = True
    except Exception:  # noqa: BLE001
        complete = False
    return found, complete


def run_flow(flow_id: str, surface: str, url: str, **kwargs: object):
    """One boundary effect; enforcement and client outcome taken from what really happened."""
    try:
        result = boundary_fetch(BOUNDARY, surface, url, **PROFILES, **kwargs)  # type: ignore[arg-type]
    except (PrivacyBoundaryError, SecurityProfileEnforcementError) as exc:
        code = type(exc).code
        if code == "boundary_transport_failed":
            logger.info("flow=%s decision=allow outcome=failed code=%s err=%s", flow_id, code, exc)
            return None, "allow", code, "reset"
        logger.info("flow=%s decision=deny code=%s err=%s", flow_id, code, exc)
        return None, "deny", code, "refused"
    logger.info("flow=%s decision=allow status=%s", flow_id, result.status)
    return result, "allow", "boundary_allowed", "connected"


enforcement: list[EnforcementObservation] = []
logs: list[ConnectionLogEntry] = []
inspections: list[PlaintextInspection] = []
captures: list[PacketCaptureObservation] = []
attempted: list[dict] = []
BODY = b'{"payload":"ordinary request body"}'


def record(
    flow_id: str, decision: str, code: str, client: str, sink: Sink, decrypted: bool = False
) -> bool:
    """An allowed flow: its own sink's real requests are the destination log."""
    enforcement.append(EnforcementObservation(flow_id, "process_guard", decision, code))
    logs.append(ConnectionLogEntry(flow_id, "client", client))
    data = sink.data()
    arrived = bool(sink.requests)
    logs.append(ConnectionLogEntry(flow_id, "destination", "connected" if arrived else "not_seen"))
    found, _ = scan(data, "tool_result")
    inspections.append(
        PlaintextInspection(
            "controlled_destination",
            sink.label,
            flow_id,
            True,
            decrypted=decrypted,
            canary_found=found,
        )
    )
    attempted.append(
        {
            "flow": flow_id,
            "decision": decision,
            "code": code,
            "client": client,
            "destination": "connected" if arrived else "not_seen",
        }
    )
    return found


def main() -> None:
    # --- allowed flows (positive controls) ---------------------------------
    res_a, dec, code, client = run_flow(
        "A",
        "data",
        f"http://127.0.0.1:{SINK_A.port}/obj",
        method="POST",
        headers=CANARY_HEADERS,
        body=BODY,
    )
    check(
        "FTF-H027-ACC1",
        "A: declared IP-literal data endpoint really connects",
        res_a is not None and res_a.status == 200 and dec == "allow",
    )
    a_found = record("A", dec, code, client, SINK_A)
    check("FTF-H027-ACC1", "A: sink really received the canary (real scan)", a_found)
    for encoding in ENCODINGS:
        check(
            "FTF-H027-ACC1",
            f"A: canary present in encoding {encoding}",
            scan(SINK_A.data(), "tool_result", (encoding,))[0],
        )

    res, dec, code, client = run_flow(
        "B",
        "judge",
        f"https://{JUDGE_NAME}:{RELAY_B.port}/v1/judge",
        method="POST",
        headers=CANARY_HEADERS,
        body=BODY,
    )
    check(
        "FTF-H027-ACC1",
        "B: declared DNS-named internal judge over verified TLS connects",
        res is not None and res.status == 200 and dec == "allow",
    )
    b_found = record("B", dec, code, client, SINK_B, decrypted=True)
    check("FTF-H027-ACC1", "B: decrypted destination bytes carry the canary", b_found)
    capture = bytes(RELAY_B.capture)
    cap_found, _ = scan(capture, "tool_result")
    check(
        "FTF-H027-NEG1",
        "B: the ciphertext capture is non-empty and shows no canary",
        len(capture) > 0 and not cap_found,
    )
    captures.append(PacketCaptureObservation("B", encrypted=True, canary_found=cap_found))

    res, dec, code, client = run_flow(
        "V6",
        "registry",
        f"http://{V6_NAME}:{SINK_V6.port}/model",
        method="POST",
        headers=CANARY_HEADERS,
        body=BODY,
    )
    check(
        "FTF-H027-ACC1",
        "V6: declared DNS name resolving to IPv6 ::1 connects",
        res is not None and res.status == 200 and dec == "allow",
    )
    check(
        "FTF-H027-ACC1", "V6: sink received it over IPv6", record("V6", dec, code, client, SINK_V6)
    )

    # --- denied flows: every destination really listens ---------------------
    denied = (
        (
            "X",
            "data",
            f"http://{UNDECLARED_NAME}:{SINK_X.port}/exfil",
            SINK_X,
            "security_profile_route_blocked",
        ),
        (
            "J",
            "judge",
            f"https://{EXTERNAL_JUDGE_NAME}:{SINK_X.port}/v1/chat/completions",
            SINK_X,
            "security_profile_route_blocked",
        ),
        (
            "MAP",
            "data",
            f"http://[::ffff:127.0.0.2]:{SINK_X.port}/exfil",
            SINK_X,
            "security_profile_route_blocked",
        ),
        (
            "R",
            "preview",
            f"http://{REBIND_NAME}:{SINK_R.port}/preview",
            SINK_R,
            "resolved_outside_boundary",
        ),
        (
            "M4",
            "data",
            f"http://{META4}:{SINK_M4.port}/latest/meta-data/",
            SINK_M4,
            "security_profile_route_blocked",
        ),
        (
            "M6",
            "data",
            f"http://[{META6}]:{SINK_M6.port}/latest/meta-data/",
            SINK_M6,
            "security_profile_route_blocked",
        ),
        ("XA", "judge", f"http://127.0.0.1:{SINK_A.port}/obj", SINK_A, None),
    )
    for flow_id, surface, url, sink, want in denied:
        before = len(sink.requests)
        res, dec, code, client = run_flow(
            flow_id, surface, url, method="POST", headers=CANARY_HEADERS, body=BODY
        )
        check(
            "FTF-H027-NEG1",
            f"{flow_id}: refused with the expected code",
            res is None and dec == "deny" and (want is None or code == want),
        )
        check(
            "FTF-H027-NEG1",
            f"{flow_id}: the real listener received nothing",
            len(sink.requests) == before,
        )
        if flow_id == "XA":
            # Cross-surface: the data endpoint used for judging. Its sink already
            # holds flow A's authorized request, so it is checked by count above,
            # not as a separate 0906 flow.
            attempted.append({"flow": flow_id, "decision": dec, "code": code})
            continue
        enforcement.append(EnforcementObservation(flow_id, "process_guard", dec, code))
        logs.append(ConnectionLogEntry(flow_id, "client", client))
        logs.append(
            ConnectionLogEntry(
                flow_id, "destination", "not_seen" if len(sink.requests) == before else "connected"
            )
        )
        if sink is not SINK_X or flow_id == "X":
            found, _ = scan(sink.data(), "tool_result")
            inspections.append(
                PlaintextInspection(
                    "controlled_destination",
                    sink.label,
                    flow_id,
                    True,
                    decrypted=False,
                    canary_found=found,
                )
            )
        else:
            found, _ = scan(SINK_X.data(), "tool_result")
            inspections.append(
                PlaintextInspection(
                    "controlled_destination",
                    f"{sink.label}-{flow_id}",
                    flow_id,
                    True,
                    decrypted=False,
                    canary_found=found,
                )
            )
        attempted.append(
            {
                "flow": flow_id,
                "decision": dec,
                "code": code,
                "client": client,
                "destination": "not_seen" if len(sink.requests) == before else "connected",
            }
        )
    res, dec, code, client = run_flow("IMDS", "data", "http://169.254.169.254:80/latest/meta-data/")
    check(
        "FTF-H027-NEG1",
        "169.254.169.254 is refused before any dial",
        res is None and code == "security_profile_route_blocked",
    )
    attempted.append({"flow": "IMDS", "decision": dec, "code": code, "destination": "unobserved"})
    check(
        "FTF-H027-NEG1",
        "no undeclared sink ever received a byte",
        not SINK_X.requests
        and not SINK_R.requests
        and not SINK_M4.requests
        and not SINK_M6.requests,
    )

    # --- content telemetry ----------------------------------------------------
    t_url = f"http://127.0.0.1:{COLLECTOR.port}/v1/events"
    sent = send_boundary_telemetry(
        BOUNDARY,
        t_url,
        "log",
        {"level": "info", "message_code": "job_started", "job_id": "job-42"},
        **PROFILES,
    )
    check(
        "FTF-H027-ACC2",
        "a content-free event reaches the declared collector",
        sent.status == 200 and len(COLLECTOR.requests) == 1,
    )
    refused_event = False
    try:
        send_boundary_telemetry(
            BOUNDARY,
            t_url,
            "log",
            {"level": "info", "message_code": "job_started", "job_id": f"{marker.value} row 7"},
            **PROFILES,
        )
    except ContentRefusedError:
        refused_event = True
    check("FTF-H027-ACC2", "an event carrying content is refused before any socket", refused_event)
    raw_code = None
    try:
        boundary_fetch(
            BOUNDARY,
            "telemetry",
            t_url,
            method="POST",
            body=json.dumps(CANARY_HEADERS).encode(),
            **PROFILES,
        )
    except RawTelemetryRefusedError as exc:
        raw_code = exc.code
    check(
        "FTF-H027-ACC2",
        "a raw canary body to the permitted collector is refused",
        raw_code == "raw_telemetry_refused",
    )
    collector_found, collector_complete = scan(COLLECTOR.data(), "trace")
    check(
        "FTF-H027-ACC2",
        "the collector holds no canary in any encoding (complete coverage)",
        not collector_found and collector_complete and len(COLLECTOR.requests) == 1,
    )

    # --- error path: a refused canary-bearing request, logged --------------
    error_start = len(LOG.getvalue())
    res, dec, code, client = run_flow(
        "ERR",
        "source",
        f"http://127.0.0.1:{DEAD_PORT}/{marker.value}",
        method="POST",
        headers=CANARY_HEADERS,
        body=BODY,
    )
    check(
        "FTF-H027-ACC1",
        "a declared but dead endpoint stays a visible transport failure",
        res is None and code == "boundary_transport_failed",
    )
    error_text = LOG.getvalue()[error_start:].encode()
    err_found, err_complete = scan(error_text, "error_path")
    check(
        "FTF-H027-ACC1",
        "the error-path log is non-empty and carries no canary",
        bool(error_text) and not err_found and err_complete,
    )
    inspections.append(
        PlaintextInspection(
            "error_path", "client-error-log", None, True, decrypted=False, canary_found=err_found
        )
    )

    log_text = LOG.getvalue().encode()
    log_found, log_complete = scan(log_text, "trace")
    check(
        "FTF-H027-ACC1",
        "the application log is real (one line per flow) and clean",
        log_text.count(b"flow=") >= 10 and not log_found and log_complete,
    )
    inspections.append(
        PlaintextInspection(
            "application_log", "client-log", None, True, decrypted=False, canary_found=log_found
        )
    )
    artifact = _TMP / "response-artifact.bin"
    artifact.write_bytes((res_a.body if res_a is not None else b"") + sent.body)
    art_found, art_complete = scan(artifact.read_bytes(), "artifact")
    check("FTF-H027-ACC1", "the saved response artifact is clean", not art_found and art_complete)
    inspections.append(
        PlaintextInspection(
            "artifact", "response-artifact", None, True, decrypted=False, canary_found=art_found
        )
    )

    # --- verify through FTF-0906 ----------------------------------------------
    def flow(fid: str, surface: str, label: str, cls: str, expected: str) -> PlannedFlow:
        return PlannedFlow(fid, SURFACE_TO_EFFECT_SURFACE[surface], label, cls, expected)

    plan = EgressPlan(
        plan_id="ftf-h027-p1-process",
        scope="process",
        policy_digest=BOUNDARY.digest,
        flows=(
            flow("A", "data", "sink-a", "internal", "allow"),
            flow("B", "judge", "judge-tls", "internal", "allow"),
            flow("V6", "registry", "registry-v6", "internal", "allow"),
            flow("X", "data", "undeclared", "external", "deny"),
            flow("J", "judge", "external-judge", "external", "deny"),
            flow("MAP", "data", "mapped-v6", "external", "deny"),
            flow("R", "preview", "rebind", "internal", "deny"),
            flow("M4", "data", "metadata-v4", "metadata", "deny"),
            flow("M6", "data", "metadata-v6", "metadata", "deny"),
        ),
    )
    evidence = EgressEvidence(tuple(enforcement), tuple(logs), tuple(inspections), tuple(captures))
    report = verify_egress(plan, evidence)
    verdicts = {v.flow_id: v for v in report.flow_verdicts}
    for fid in ("A", "B", "V6", "X", "J", "MAP", "R", "M4", "M6"):
        check(
            "FTF-H027-ACC1", f"verify_egress: flow {fid} passed", verdicts[fid].status == "passed"
        )
    check(
        "FTF-H027-SCOPE",
        "overall partial: workflow_history is P2, never reported passed",
        report.status == "partial" and report.not_exercised == ("workflow_history",),
    )

    # Fault rows through the same verifier: encrypted capture alone is no clearance.
    no_b_dest = tuple(
        i for i in inspections if not (i.store == "controlled_destination" and i.flow_id == "B")
    )
    capture_only = verify_egress(
        plan, EgressEvidence(tuple(enforcement), tuple(logs), no_b_dest, tuple(captures))
    )
    check(
        "FTF-H027-FAULT",
        "dropping B's decrypted inspection leaves only ciphertext -> not passed",
        {v.flow_id: v for v in capture_only.flow_verdicts}["B"].status != "passed"
        and capture_only.status != "passed",
    )
    # A real raw-socket bypass to the undeclared sink lands, and is caught.
    raw = socket.create_connection(("127.0.0.2", SINK_X.port), timeout=5)
    raw.sendall(
        (
            f"POST /bypass HTTP/1.1\r\nHost: x\r\nX-Canary: {marker.value}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode()
    )
    raw.recv(1024)
    raw.close()
    bypass_found, _ = scan(SINK_X.data(), "tool_result")
    check("FTF-H027-FAULT", "the raw bypass really landed with the canary", bypass_found)
    bypassed_logs = tuple(
        ConnectionLogEntry("X", e.side, "connected") if e.flow_id == "X" else e for e in logs
    )
    bypassed_insp = tuple(
        PlaintextInspection(
            i.store, i.store_id, i.flow_id, True, decrypted=False, canary_found=bypass_found
        )
        if (i.store == "controlled_destination" and i.flow_id == "X")
        else i
        for i in inspections
    )
    bypass_report = verify_egress(
        plan, EgressEvidence(tuple(enforcement), bypassed_logs, bypassed_insp, tuple(captures))
    )
    bypass_verdict = {v.flow_id: v for v in bypass_report.flow_verdicts}["X"]
    check(
        "FTF-H027-FAULT",
        "the bypass is reported failed / unauthorized_transfer",
        bypass_verdict.status == "failed" and "unauthorized_transfer" in bypass_verdict.codes,
    )

    # --- content-free Q23 evidence record -------------------------------------
    module_digests = {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in (
            "packages/security/privacy_boundary.py",
            "packages/security/url_policy.py",
            "packages/security/outbound_guard.py",
            "packages/security/security_profile_enforcement.py",
            "packages/security/content_free_telemetry.py",
        )
    }
    record_doc = {
        "task": "FTF-H027-P1",
        "scenario": "Q23",
        "scope": "process",
        "topology": "single runner loopback; IPv4 127.0.0.1-3, ::1, metadata addresses on lo",
        "boundary_id": BOUNDARY.boundary_id,
        "policy_digest": BOUNDARY.digest,
        "enforcement_version": module_digests,
        "attempted_routes": attempted,
        "inspected_stores": list(report.inspected_stores),
        "not_exercised": list(report.not_exercised),
        "threat_model_exclusions": list(BOUNDARY.threat_model_exclusions),
        "report_status": report.status,
        "report_sha256": report.report_sha256,
    }
    text = json.dumps(record_doc, sort_keys=True, indent=1)
    check(
        "FTF-H027-ACC1",
        "the evidence record itself is content-free (no canary, no host name)",
        not scan(text.encode(), "artifact")[0] and JUDGE_NAME not in text,
    )
    out = ROOT / "reports" / "ftf-h027-p1-evidence.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text + "\n")
    print(text)
    print("evidence sha256:", hashlib.sha256(out.read_bytes()).hexdigest())

    if FAILURES:
        print("\n".join(FAILURES))
        print(f"FTF-H027-P1 integration: {len(FAILURES)} failure(s)")
        sys.exit(1)
    print("FTF-H027-P1 integration: all checks passed")


if __name__ == "__main__":
    main()
