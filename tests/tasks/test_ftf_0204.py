#!/usr/bin/env python3
"""FTF-0204 -- `/v1/models` discovery.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_0203.py` (already CERTIFIED) established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (the same KV v2 read shape `packages/litellm_client/
    secret_manager_integration.py` (FTF-0202, already CERTIFIED)
    actually calls, exercised unmodified);
  - real local HTTPS servers standing in for the organization's LiteLLM
    gateway, with a real self-signed CA this suite generates and installs
    into the sandbox's own system trust store for the duration of the
    run (removed again in `atexit`), so the "discovered" happy path
    exercises real, unmodified TLS certificate validation.

This task's own negative_acceptance ("Metadata-only or stale deleted
model is not callable; auth errors fail closed instead of retaining
permission") is covered by two dedicated real round-trip checks:
`test_metadata_only_model_is_never_callable` and
`test_removed_model_is_not_retained_across_calls`/
`test_auth_failure_fails_closed_not_retained`.

Run directly: python3 tests/tasks/test_ftf_0204.py
"""

from __future__ import annotations

import atexit
import json
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveryState,
    ModelMetadataStatus,
    _classify_guard_failure,
    _hash_models,
    _parse_models,
    discover_models,
)
from packages.security.outbound_guard import OutboundGuardError  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Real local secret-backend fixture (OpenBao KV v2 read subset) ----------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-inference-key-0204"
_SECRET_TOKEN = "test-vault-token"


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _json(self, status: int, payload: dict[str, object] | None) -> None:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        token = self.headers.get("X-Vault-Token")
        if token != _SECRET_TOKEN:
            self._json(403, {"errors": ["permission denied"]})
            return
        if self.path == f"/v1/{_SECRET_MOUNT}/data/{_SECRET_PATH}":
            self._json(
                200,
                {"data": {"data": {_SECRET_FIELD: _SECRET_VALUE}, "metadata": {"version": 1}}},
            )
        else:
            self._json(404, {"errors": []})


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
_SECRET_PORT = _SECRET_SERVER.server_address[1]
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)

_SECRET_CONFIG = OpenBaoConfig(address=f"http://127.0.0.1:{_SECRET_PORT}", token=_SECRET_TOKEN)


def _secret_ref(path: str = _SECRET_PATH) -> str:
    return f"openbao://{_SECRET_MOUNT}/{path}/{_SECRET_FIELD}"


# -- Real local TLS fixture: same convention as tests/tasks/test_ftf_0203.py -

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0204-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0204-test-ca.crt")
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key = _TLS_TMP / "ca.key"
    ca_crt = _TLS_TMP / "ca.crt"
    leaf_key = _TLS_TMP / "leaf.key"
    leaf_csr = _TLS_TMP / "leaf.csr"
    leaf_crt = _TLS_TMP / "leaf.crt"
    leaf_ext = _TLS_TMP / "leaf.ext"

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
            "/CN=FTF-0204 Test CA",
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
if _UPDATE_CA_AVAILABLE:
    _TRUSTED_LEAF, _TRUSTED_KEY = _generate_trusted_server_cert()
else:
    print(
        "FTF-0204: this sandbox has no openssl/update-ca-certificates -- the "
        "live TLS-backed checks are skipped and disclosed here; the pure "
        "classifier/parser/hash unit checks still run and still count. "
        "GitHub Actions' ubuntu-latest runner has both tools."
    )


def _policy_for(port: int) -> ConnectionPolicy:
    import ipaddress

    return ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


def _start_https_server(handler_cls: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    assert _TRUSTED_LEAF is not None and _TRUSTED_KEY is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(_TRUSTED_LEAF), keyfile=str(_TRUSTED_KEY))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


class _MutableGatewayHandler(BaseHTTPRequestHandler):
    """A real HTTPS gateway whose `/v1/models` and `/model/info` bodies can
    be swapped mid-run (module-level mutable state below) -- used to prove
    a second real, live discovery call never retains a name the first call
    saw but the current authoritative response no longer contains."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        auth = self.headers.get("Authorization", "")
        if self.path == "/v1/models":
            if not _GATEWAY_STATE.authorized:
                self._respond(401, b'{"error":"invalid token"}')
                return
            if auth != f"Bearer {_SECRET_VALUE}":
                self._respond(401, b'{"error":"invalid token"}')
                return
            self._respond(200, _GATEWAY_STATE.models_body)
        elif self.path == "/model/info":
            self._respond(200, _GATEWAY_STATE.model_info_body)
        else:
            self._respond(404, b"")


@dataclass
class _GatewayState:
    authorized: bool
    models_body: bytes
    model_info_body: bytes


_GATEWAY_STATE = _GatewayState(
    authorized=True,
    models_body=b'{"data":[{"id":"gpt-a"},{"id":"gpt-b"}]}',
    model_info_body=b'{"data":[{"model_name":"gpt-a"}]}',
)


def _make_static_handler(
    *,
    valid_token: str = _SECRET_VALUE,
    models_status: int = 200,
    models_body: bytes = b'{"data":[{"id":"gpt-a"},{"id":"gpt-b"},{"id":123}]}',
    model_info_status: int = 200,
    model_info_body: bytes = b'{"data":[{"model_name":"gpt-a"}]}',
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            auth = self.headers.get("Authorization", "")
            if self.path == "/v1/models":
                if auth != f"Bearer {valid_token}":
                    self._respond(401, b'{"error":"invalid token"}')
                    return
                self._respond(models_status, models_body)
            elif self.path == "/model/info":
                self._respond(model_info_status, model_info_body)
            else:
                self._respond(404, b"")

    return _Handler


# -- Tests --------------------------------------------------------------


def test_discovered_lists_models_with_correct_metadata_status() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler()
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "a real reachable+authorized gateway reports discovered",
        result.state == DiscoveryState.DISCOVERED,
    )
    check(
        "FTF-0204",
        "model_count reflects only well-formed entries (2 of 3)",
        result.model_count == 2,
    )
    ids = {m.id for m in result.models}
    check("FTF-0204", "both real authorized model IDs are present", ids == {"gpt-a", "gpt-b"})
    by_id = {m.id: m for m in result.models}
    check(
        "FTF-0204",
        "an authorized model present in /model/info is metadata_status=enriched",
        by_id["gpt-a"].metadata_status == ModelMetadataStatus.ENRICHED,
    )
    check(
        "FTF-0204",
        "an authorized model absent from /model/info is metadata_status=limited (Section 3.2)",
        by_id["gpt-b"].metadata_status == ModelMetadataStatus.LIMITED,
    )
    check(
        "FTF-0204",
        "checked_at is a real timezone-aware timestamp",
        result.checked_at.tzinfo is not None,
    )
    check(
        "FTF-0204",
        "content_hash is a real deterministic sha256 hex digest",
        isinstance(result.content_hash, str) and len(result.content_hash) == 64,
    )
    check(
        "FTF-0204",
        "content_hash matches an independent recomputation over the same ids",
        result.content_hash == _hash_models(["gpt-a", "gpt-b"]),
    )


def test_metadata_only_model_is_never_callable() -> None:
    """This task's own negative_acceptance: "Metadata-only... model is not
    callable." `/model/info` here advertises `unlisted-model`, which
    `/v1/models` itself never returns -- Section 3.2's "no authorization
    union": enrichment is a left join onto the authorized list, never a
    grant of visibility by itself."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        models_body=b'{"data":[{"id":"gpt-a"}]}',
        model_info_body=b'{"data":[{"model_name":"gpt-a"},{"model_name":"unlisted-model"}]}',
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    ids = {m.id for m in result.models}
    check(
        "FTF-0204",
        "a name known only through /model/info enrichment is never added to the callable list",
        "unlisted-model" not in ids,
    )
    check("FTF-0204", "the real authorized model is still present", ids == {"gpt-a"})
    check(
        "FTF-0204",
        "model_count reflects only the authorized list, not the enrichment list",
        result.model_count == 1,
    )


def test_removed_model_is_not_retained_across_calls() -> None:
    """This task's own negative_acceptance: "...stale deleted model is not
    callable." Two real, live discovery calls against the same mutable
    gateway: the second call's authoritative response no longer contains
    a model the first call saw -- the result must not retain it (Section
    3.3: "removed names immediately become unselectable")."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _GATEWAY_STATE.authorized = True
    _GATEWAY_STATE.models_body = b'{"data":[{"id":"gpt-a"},{"id":"gpt-old"}]}'
    _GATEWAY_STATE.model_info_body = b'{"data":[]}'
    server = _start_https_server(_MutableGatewayHandler)
    port = server.server_address[1]

    first = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "first real call sees the real then-current model including gpt-old",
        {m.id for m in first.models} == {"gpt-a", "gpt-old"},
    )

    # The real gateway's own authoritative inventory changes -- gpt-old is
    # deleted upstream (a real, live event this suite forces, not a
    # simulated cache eviction).
    _GATEWAY_STATE.models_body = b'{"data":[{"id":"gpt-a"}]}'

    second = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    ids = {m.id for m in second.models}
    check(
        "FTF-0204",
        "a second real call against the same gateway never retains a model the "
        "current authoritative response no longer contains",
        "gpt-old" not in ids,
    )
    check(
        "FTF-0204",
        "the second call's model list reflects only the current real inventory",
        ids == {"gpt-a"},
    )


def test_auth_failure_fails_closed_not_retained() -> None:
    """This task's own negative_acceptance: "auth errors fail closed
    instead of retaining permission." A real successful discovery is
    followed by a real live credential revocation (the gateway starts
    rejecting the same token) -- the next call must report invalid_token
    with an empty model list, never the previously observed inventory."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _GATEWAY_STATE.authorized = True
    _GATEWAY_STATE.models_body = b'{"data":[{"id":"gpt-a"},{"id":"gpt-b"}]}'
    _GATEWAY_STATE.model_info_body = b'{"data":[]}'
    server = _start_https_server(_MutableGatewayHandler)
    port = server.server_address[1]

    first = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "real credential is initially authorized with a real 2-model inventory",
        first.model_count == 2,
    )

    _GATEWAY_STATE.authorized = False  # a real, live key revocation
    revoked = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "a real gateway 401 on a resolved credential reports invalid_token, not discovered",
        revoked.state == DiscoveryState.INVALID_TOKEN,
    )
    check(
        "FTF-0204",
        "a revoked credential never retains the previously observed model list",
        revoked.models == (),
    )
    check(
        "FTF-0204",
        "a revoked credential never carries a stale or fabricated model_count",
        revoked.model_count is None,
    )
    check(
        "FTF-0204",
        "a revoked credential never carries a stale content_hash",
        revoked.content_hash is None,
    )


def test_management_enrichment_failure_does_not_invalidate_discovery() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        models_body=b'{"data":[{"id":"gpt-a"}]}',
        model_info_status=403,
        model_info_body=b'{"error":"forbidden"}',
    )
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "enrichment-403 does not invalidate a successful standard model list (Section 3.1)",
        result.state == DiscoveryState.DISCOVERED,
    )
    check(
        "FTF-0204",
        "every model falls back to metadata_status=limited when enrichment is forbidden",
        all(m.metadata_status == ModelMetadataStatus.LIMITED for m in result.models),
    )


def test_malformed_models_response_never_invents_a_list() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(models_status=200, models_body=b"not json at all")
    server = _start_https_server(handler)
    port = server.server_address[1]
    result = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "a malformed /v1/models body reports malformed_response, never discovered",
        result.state == DiscoveryState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0204", "a malformed response never carries an invented model list", result.models == ()
    )
    check(
        "FTF-0204",
        "a malformed response never carries an invented model_count",
        result.model_count is None,
    )
    check(
        "FTF-0204",
        "a malformed response never carries an invented content_hash",
        result.content_hash is None,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    policy = _policy_for(1)

    not_found = discover_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/does-not-exist"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0204",
        "an unknown secret path reports secret_not_found",
        not_found.state == DiscoveryState.SECRET_NOT_FOUND,
    )
    check(
        "FTF-0204",
        "secret_not_found never attempts a gateway call, never carries models",
        not_found.models == (),
    )

    denied = discover_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong"),
        policy=policy,
    )
    check(
        "FTF-0204",
        "a real backend 403 on the resolving token reports secret_access_denied",
        denied.state == DiscoveryState.SECRET_ACCESS_DENIED,
    )

    unsupported = discover_models(
        base_url="https://127.0.0.1:1",
        secret_ref="vault://team/litellm/key",
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0204",
        "an older vault:// reference with no real backend reports "
        "secret_backend_unsupported, never crashes",
        unsupported.state == DiscoveryState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = discover_models(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=policy,
    )
    check(
        "FTF-0204",
        "no secret backend configured in this environment reports "
        "secret_backend_error, never a fabricated success",
        no_config.state == DiscoveryState.SECRET_BACKEND_ERROR,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    handler = _make_static_handler()
    if _UPDATE_CA_AVAILABLE:
        server = _start_https_server(handler)
        port = server.server_address[1]
    else:
        port = 1
    policy = ConnectionPolicy(name="test-no-allowlist", allowed_schemes=frozenset({"https"}))
    result = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0204",
        "a real destination outside the connection policy's own allowlist is denied "
        "before any socket opens, reported as destination_denied",
        result.state == DiscoveryState.DESTINATION_DENIED,
    )


def test_parse_models_drops_malformed_entries_not_the_whole_response() -> None:
    check(
        "FTF-0204",
        "a well-formed data list keeps only entries with a real string id, in order, deduplicated",
        _parse_models(b'{"data":[{"id":"a"},{"id":"b"},{"notid":1},{"id":"a"}]}') == ["a", "b"],
    )
    check(
        "FTF-0204",
        "a non-dict top-level body is malformed (None), never an empty list",
        _parse_models(b"[1,2,3]") is None,
    )
    check(
        "FTF-0204",
        "a missing data key is malformed (None)",
        _parse_models(b'{"foo":"bar"}') is None,
    )
    check("FTF-0204", "invalid JSON is malformed (None)", _parse_models(b"not json") is None)


def test_hash_models_is_order_independent_and_deterministic() -> None:
    check(
        "FTF-0204",
        "the content hash does not depend on gateway response ordering",
        _hash_models(["gpt-a", "gpt-b"]) == _hash_models(["gpt-b", "gpt-a"]),
    )
    check(
        "FTF-0204",
        "a different model set produces a different hash",
        _hash_models(["gpt-a"]) != _hash_models(["gpt-a", "gpt-b"]),
    )
    check(
        "FTF-0204",
        "an empty authorized set still gets a real deterministic hash, never a sentinel",
        _hash_models([]) == _hash_models([]) and isinstance(_hash_models([]), str),
    )


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0204",
        "a real socket.gaierror classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == DiscoveryState.DNS_FAILED,
    )
    check(
        "FTF-0204",
        "a real ssl.SSLCertVerificationError classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == DiscoveryState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0204",
        "a real TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == DiscoveryState.TIMEOUT,
    )
    check(
        "FTF-0204",
        "a real ConnectionRefusedError classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == DiscoveryState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0204",
        "any other real OSError classifies as unreachable, never invented as discovered",
        _classify_guard_failure(_wrapped(OSError("something else"))) == DiscoveryState.UNREACHABLE,
    )


# -- Fault injection (Law 1): a deliberate regression this suite must catch -

_MODULE_PATH = ROOT / "packages" / "litellm_client" / "v1_models_discovery.py"


def test_fault_injection_reintroducing_authorization_union_is_caught() -> None:
    """Mutates the real, on-disk `v1_models_discovery.py` so the returned
    model list is built from `model_ids` unioned with `enrichment_ids`
    instead of `model_ids` alone -- reintroducing exactly the Section 3.2
    "authorization union" bug this task's own negative_acceptance forbids
    ("metadata-only... model is not callable"). Runs the mutated module
    against a real gateway in a fresh subprocess and proves the
    unauthorized, metadata-only name genuinely leaks into the result --
    not merely that the mutation was syntactically applied. Restores the
    file byte-identical afterward."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_static_handler(
        models_body=b'{"data":[{"id":"gpt-a"}]}',
        model_info_body=b'{"data":[{"model_name":"gpt-a"},{"model_name":"unlisted-model"}]}',
    )
    server = _start_https_server(handler)
    port = server.server_address[1]

    baseline = discover_models(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0204",
        "baseline (unmutated): a metadata-only name never appears in the real result",
        "unlisted-model" not in {m.id for m in baseline.models},
    )

    original = _MODULE_PATH.read_bytes()
    try:
        text = original.decode("utf-8")
        pattern = re.compile(r"for model_id in model_ids\n    \)")
        match = pattern.search(text)
        check(
            "FTF-0204",
            "the fault-injection target line is present in the tracked module",
            match is not None,
        )
        mutated = pattern.sub(
            "for model_id in sorted(set(model_ids) | enrichment_ids)\n    )", text
        )
        check("FTF-0204", "the mutation actually changed the file", mutated != text)
        _MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from packages.litellm_client.v1_models_discovery import discover_models
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.security.url_policy import ConnectionPolicy
import ipaddress

policy = ConnectionPolicy(
    name="p",
    allowed_schemes=frozenset({{"https"}}),
    allowed_ports=frozenset({{{port}}}),
    explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
)
result = discover_models(
    base_url="https://127.0.0.1:{port}",
    secret_ref={_secret_ref()!r},
    secret_config=OpenBaoConfig(address={_SECRET_CONFIG.address!r}, token={_SECRET_TOKEN!r}),
    policy=policy,
)
print("IDS=" + ",".join(sorted(m.id for m in result.models)))
"""
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0204",
            "fault injection: unioning enrichment into the authorized list genuinely "
            f"lets a metadata-only name become callable (stdout={result.stdout!r} "
            f"stderr={result.stderr!r})",
            result.returncode == 0 and "unlisted-model" in result.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0204",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0204 /v1/models discovery: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0204 /v1/models discovery: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
