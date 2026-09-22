#!/usr/bin/env python3
"""FTF-0203 -- Connection validation.

Runnable without any database and without any real external network
egress -- this suite builds and drives real local servers itself,
matching the no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_h006.py` and `tests/tasks/test_ftf_0202.py` already
established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (implementing exactly the KV v2 read shape
    `packages/litellm_client/secret_manager_integration.py` (FTF-0202,
    already CERTIFIED) actually calls -- this suite exercises that real,
    unmodified module against it, never a mock of `resolve_secret_ref`
    itself);
  - real local HTTPS servers standing in for the organization's LiteLLM
    gateway, with a real self-signed CA this suite generates and installs
    into the sandbox's own system trust store for the duration of the
    run (removed again in `atexit`) -- so the "connected" happy path
    exercises real, unmodified TLS certificate validation (via
    `packages/security/outbound_guard.py`'s own `ssl.create_default_context()`,
    never a relaxed/patched context), and the certificate-error path uses
    a second, deliberately never-installed self-signed leaf against that
    same real validation.

Section 3.1's acceptance clause ("Validate real gateway TLS/auth/
reachability and safe management/inference capabilities separately") and
negative_acceptance ("Timeout, certificate error, invalid token and
management-403 produce distinct sanitized states, not connected badge")
are covered by real, live socket round trips wherever a real round trip
is feasible in this sandbox; DNS-failure classification (this sandbox has
no reliable, egress-independent way to force a real `socket.gaierror`
without depending on external DNS behavior) is proven as a direct,
real-exception-instance unit check of the classifier function itself --
matching Section 84.4's "test doubles... exercise isolated unit... cases."

Run directly: python3 tests/tasks/test_ftf_0203.py
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.connection_validation import (  # noqa: E402
    InferenceCheckState,
    ManagementCheckState,
    _classify_guard_failure,
    _parse_model_count,
    default_connection_validation_policy,
    validate_connection,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
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
_SECRET_VALUE = "sk-real-inference-key-0203"
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
                {
                    "data": {
                        "data": {_SECRET_FIELD: _SECRET_VALUE},
                        "metadata": {"version": 1},
                    }
                },
            )
        elif self.path == f"/v1/{_SECRET_MOUNT}/data/litellm/expired":
            self._json(
                200,
                {
                    "data": {
                        "data": {_SECRET_FIELD: _SECRET_VALUE},
                        "metadata": {
                            "version": 1,
                            "custom_metadata": {"expires_at": "2000-01-01T00:00:00Z"},
                        },
                    }
                },
            )
        elif self.path == f"/v1/{_SECRET_MOUNT}/data/litellm/broken":
            self._json(500, {"errors": ["internal"]})
        else:
            self._json(404, {"errors": []})


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
_SECRET_PORT = _SECRET_SERVER.server_address[1]
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)

_SECRET_CONFIG = OpenBaoConfig(address=f"http://127.0.0.1:{_SECRET_PORT}", token=_SECRET_TOKEN)


def _secret_ref(path: str = _SECRET_PATH) -> str:
    return f"openbao://{_SECRET_MOUNT}/{path}/{_SECRET_FIELD}"


# -- Real local TLS fixture: a genuine CA installed into the sandbox's own
#    system trust store, plus a genuine untrusted self-signed leaf -------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0203-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0203-test-ca.crt")
# Installing into the system trust store needs root -- neither this
# sandbox's own default user nor GitHub Actions' `runner` user owns
# `/usr/local/share/ca-certificates/` directly, so every privileged step
# below goes through `sudo` (passwordless on both, confirmed via `sudo -n
# true`) rather than a raw `Path.write_bytes`/un-prefixed subprocess call.
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    """A leaf cert for `127.0.0.1` signed by a real, freshly generated CA
    this function installs into the sandbox's own system trust store
    (removed again in `atexit`) -- so `ssl.create_default_context()`, used
    completely unmodified by `packages/security/outbound_guard.py`, genuinely
    accepts it."""
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
            "/CN=FTF-0203 Test CA",
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


def _generate_untrusted_server_cert() -> tuple[Path, Path]:
    """A self-signed leaf never installed anywhere -- a real TLS peer this
    sandbox's real default trust store genuinely refuses."""
    leaf_key = _TLS_TMP / "untrusted-leaf.key"
    leaf_crt = _TLS_TMP / "untrusted-leaf.crt"
    _run_openssl(
        [
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(leaf_key),
            "-out",
            str(leaf_crt),
            "-days",
            "2",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ]
    )
    return leaf_crt, leaf_key


def _make_gateway_handler(
    *,
    valid_token: str,
    models_status: int = 200,
    models_body: bytes = b'{"data":[{"id":"gpt-a"},{"id":"gpt-b"},{"id":123}]}',
    model_info_status: int = 200,
    model_info_body: bytes = b'{"data":[{"id":"gpt-a"}]}',
    hang_on: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class _GatewayHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if hang_on is not None and self.path == hang_on:
                # A real accepted connection that never sends a response --
                # the client's own bounded read timeout is what must fire.
                threading.Event().wait(5)
                return
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

    return _GatewayHandler


def _start_https_server(
    handler_cls: type[BaseHTTPRequestHandler], certfile: Path | None, keyfile: Path | None
) -> ThreadingHTTPServer:
    # Only ever called after an `if not _UPDATE_CA_AVAILABLE: return` guard,
    # which is when the module-level cert paths below are populated -- the
    # `Path | None` parameter type matches those globals' own real type so
    # mypy does not need a per-call-site assertion at every one of this
    # function's many call sites.
    assert certfile is not None and keyfile is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


_TRUSTED_LEAF: Path | None = None
_TRUSTED_KEY: Path | None = None
_UNTRUSTED_LEAF: Path | None = None
_UNTRUSTED_KEY: Path | None = None
if _UPDATE_CA_AVAILABLE:
    _TRUSTED_LEAF, _TRUSTED_KEY = _generate_trusted_server_cert()
    _UNTRUSTED_LEAF, _UNTRUSTED_KEY = _generate_untrusted_server_cert()
else:
    print(
        "FTF-0203: this sandbox has no openssl/update-ca-certificates -- the "
        "live TLS-backed checks (connected/invalid_token/management/"
        "malformed/timeout/certificate_error) are skipped and disclosed "
        "here; the pure classifier/parser unit checks still run and still "
        "count. GitHub Actions' ubuntu-latest runner has both tools."
    )


def _policy_for(port: int) -> ConnectionPolicy:
    import ipaddress

    return ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


# -- Tests --------------------------------------------------------------


def test_connected_and_management_available() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token=_SECRET_VALUE)
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0203",
        "a real reachable+authorized gateway reports connected",
        result.inference_state == InferenceCheckState.CONNECTED,
    )
    check(
        "FTF-0203",
        "model_count reflects only well-formed entries (2 of 3)",
        result.model_count == 2,
    )
    check(
        "FTF-0203",
        "management enrichment reports available for a real 200 JSON object",
        result.management_state == ManagementCheckState.AVAILABLE,
    )
    check(
        "FTF-0203",
        "checked_at is a real timezone-aware timestamp",
        result.checked_at.tzinfo is not None,
    )


def test_invalid_token_is_distinct_from_connected() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token=_SECRET_VALUE)
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    # A real, live wrong-secret path: a different vault field holding a
    # value the gateway never accepts.
    wrong_ref = _secret_ref()
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=wrong_ref,
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong-token"),
        policy=_policy_for(port),
    )
    # Wrong vault token denies secret resolution itself first (SECRET_ACCESS_DENIED) --
    # a distinct sanitized state, never "connected".
    check(
        "FTF-0203",
        "a denied secret backend token never reports connected",
        result.inference_state != InferenceCheckState.CONNECTED,
    )
    check(
        "FTF-0203",
        "a denied secret backend token reports secret_access_denied",
        result.inference_state == InferenceCheckState.SECRET_ACCESS_DENIED,
    )

    # Now resolve the secret for real but present it to a gateway that
    # rejects it -- the real "invalid_token" negative_acceptance case.
    handler_rejecting = _make_gateway_handler(valid_token="a-completely-different-key")
    server2 = _start_https_server(handler_rejecting, _TRUSTED_LEAF, _TRUSTED_KEY)
    port2 = server2.server_address[1]
    result2 = validate_connection(
        base_url=f"https://127.0.0.1:{port2}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port2),
    )
    check(
        "FTF-0203",
        "a real gateway 401 on a resolved credential reports invalid_token, not connected",
        result2.inference_state == InferenceCheckState.INVALID_TOKEN,
    )
    check("FTF-0203", "invalid_token never carries a model_count", result2.model_count is None)
    check(
        "FTF-0203",
        "invalid_token never attempts the management dimension",
        result2.management_state == ManagementCheckState.NOT_ATTEMPTED,
    )


def test_management_forbidden_does_not_invalidate_connected() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(
        valid_token=_SECRET_VALUE, model_info_status=403, model_info_body=b'{"error":"forbidden"}'
    )
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0203",
        "management-403 does not invalidate a successful standard model list (Section 3.1)",
        result.inference_state == InferenceCheckState.CONNECTED,
    )
    check(
        "FTF-0203",
        "management-403 is reported as its own distinct forbidden state, not folded into inference",
        result.management_state == ManagementCheckState.FORBIDDEN,
    )
    check(
        "FTF-0203",
        "model_count is still populated alongside a forbidden management state",
        result.model_count == 2,
    )


def test_management_not_found_is_distinct_from_forbidden() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(
        valid_token=_SECRET_VALUE, model_info_status=404, model_info_body=b""
    )
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0203",
        "a real 404 on enrichment reports not_found, a state distinct from forbidden",
        result.management_state == ManagementCheckState.NOT_FOUND,
    )
    check(
        "FTF-0203",
        "connection still reports connected on 404 enrichment",
        result.inference_state == InferenceCheckState.CONNECTED,
    )


def test_malformed_models_response_never_invents_a_count() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(
        valid_token=_SECRET_VALUE, models_status=200, models_body=b"not json at all"
    )
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0203",
        "a malformed /v1/models body reports malformed_response, never connected",
        result.inference_state == InferenceCheckState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0203",
        "a malformed response never carries an invented model_count",
        result.model_count is None,
    )
    check(
        "FTF-0203",
        "a malformed response never attempts the management dimension",
        result.management_state == ManagementCheckState.NOT_ATTEMPTED,
    )


def test_certificate_error_is_real_and_distinct() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token=_SECRET_VALUE)
    server = _start_https_server(handler, _UNTRUSTED_LEAF, _UNTRUSTED_KEY)
    port = server.server_address[1]
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0203",
        "a real, never-installed self-signed leaf is genuinely refused by "
        "unmodified default TLS certificate validation, reported as "
        "certificate_error (not connected, not a generic failure)",
        result.inference_state == InferenceCheckState.CERTIFICATE_ERROR,
    )


def test_timeout_is_real_and_distinct() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token=_SECRET_VALUE, hang_on="/v1/models")
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        timeout=0.5,
    )
    check(
        "FTF-0203",
        "a real accepted connection that never answers within the bounded "
        "timeout is reported as timeout, not a hang and not connected",
        result.inference_state == InferenceCheckState.TIMEOUT,
    )


def test_connection_refused_is_real_and_distinct() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()  # Real: no listener on this port for the rest of the run.

    import ipaddress

    policy = ConnectionPolicy(
        name="test-refused",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({closed_port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )
    result = validate_connection(
        base_url=f"https://127.0.0.1:{closed_port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        timeout=2.0,
    )
    check(
        "FTF-0203",
        "a real closed TCP port is reported as connection_refused, not a generic unreachable",
        result.inference_state == InferenceCheckState.CONNECTION_REFUSED,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    # base_url/policy below are never actually dialed -- every case here
    # fails during secret resolution, before any socket opens.
    policy = _policy_for(1)

    not_found = validate_connection(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/does-not-exist"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0203",
        "an unknown secret path reports secret_not_found",
        not_found.inference_state == InferenceCheckState.SECRET_NOT_FOUND,
    )
    check(
        "FTF-0203",
        "secret_not_found never attempts a gateway call (management not_attempted)",
        not_found.management_state == ManagementCheckState.NOT_ATTEMPTED,
    )

    denied = validate_connection(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong"),
        policy=policy,
    )
    check(
        "FTF-0203",
        "a real backend 403 on the resolving token reports secret_access_denied",
        denied.inference_state == InferenceCheckState.SECRET_ACCESS_DENIED,
    )

    expired = validate_connection(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/expired"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0203",
        "a real expired custom_metadata.expires_at reports secret_expired",
        expired.inference_state == InferenceCheckState.SECRET_EXPIRED,
    )

    backend_error = validate_connection(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref("litellm/broken"),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0203",
        "a real repeated backend 500 reports secret_backend_error",
        backend_error.inference_state == InferenceCheckState.SECRET_BACKEND_ERROR,
    )

    unsupported = validate_connection(
        base_url="https://127.0.0.1:1",
        secret_ref="vault://team/litellm/key",
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0203",
        "an older vault:// reference with no real backend reports "
        "secret_backend_unsupported, never crashes",
        unsupported.inference_state == InferenceCheckState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = validate_connection(
        base_url="https://127.0.0.1:1",
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=policy,
    )
    check(
        "FTF-0203",
        "no secret backend configured in this environment reports "
        "secret_backend_error, never a fabricated success",
        no_config.inference_state == InferenceCheckState.SECRET_BACKEND_ERROR,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    handler = _make_gateway_handler(valid_token=_SECRET_VALUE)
    if _UPDATE_CA_AVAILABLE:
        server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
        port = server.server_address[1]
    else:
        port = 1
    # A real policy that does not allowlist loopback -- the same real
    # `validate_resolved_address` denial path a production deployment
    # relies on for SSRF protection.
    policy = ConnectionPolicy(name="test-no-allowlist", allowed_schemes=frozenset({"https"}))
    result = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
    )
    check(
        "FTF-0203",
        "a real destination outside the connection policy's own allowlist is "
        "denied before any socket opens, reported as destination_denied",
        result.inference_state == InferenceCheckState.DESTINATION_DENIED,
    )


def test_default_policy_is_https_only_no_redirects() -> None:
    policy = default_connection_validation_policy()
    check(
        "FTF-0203",
        "the default validation policy allows only https",
        policy.allowed_schemes == frozenset({"https"}),
    )
    check(
        "FTF-0203",
        "the default validation policy does not follow redirects (Section 65.4 default)",
        policy.allow_redirects is False,
    )


def test_parse_model_count_drops_malformed_entries_not_the_whole_response() -> None:
    check(
        "FTF-0203",
        "a well-formed data list counts only entries with a real string id",
        _parse_model_count(b'{"data":[{"id":"a"},{"id":"b"},{"notid":1}]}') == 2,
    )
    check(
        "FTF-0203",
        "a non-dict top-level body is malformed (None), never zero",
        _parse_model_count(b"[1,2,3]") is None,
    )
    check(
        "FTF-0203",
        "a missing data key is malformed (None)",
        _parse_model_count(b'{"foo":"bar"}') is None,
    )
    check("FTF-0203", "invalid JSON is malformed (None)", _parse_model_count(b"not json") is None)


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0203",
        "a real socket.gaierror classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == InferenceCheckState.DNS_FAILED,
    )
    check(
        "FTF-0203",
        "a real ssl.SSLCertVerificationError classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == InferenceCheckState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0203",
        "a real generic ssl.SSLError classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLError("tls failure")))
        == InferenceCheckState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0203",
        "a real TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == InferenceCheckState.TIMEOUT,
    )
    check(
        "FTF-0203",
        "a real ConnectionRefusedError classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == InferenceCheckState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0203",
        "any other real OSError classifies as unreachable, never invented as connected",
        _classify_guard_failure(_wrapped(OSError("something else")))
        == InferenceCheckState.UNREACHABLE,
    )


# -- Fault injection (Law 1): a deliberate regression this suite must catch -


_MODULE_PATH = ROOT / "packages" / "litellm_client" / "connection_validation.py"

_FABRICATED_KEY = "fabricated-key-despite-denial"

_FAULT_INJECTION_PROBE = """
import sys
sys.path.insert(0, {root!r})
from packages.litellm_client.connection_validation import validate_connection, InferenceCheckState
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.security.url_policy import ConnectionPolicy
import ipaddress

policy = ConnectionPolicy(
    name="p",
    allowed_schemes=frozenset({{"https"}}),
    allowed_ports=frozenset({{{port}}}),
    explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
)
result = validate_connection(
    base_url="https://127.0.0.1:{port}",
    secret_ref="openbao://secret/litellm/org1/api_key",
    secret_config=OpenBaoConfig(
        address="http://127.0.0.1:{secret_port}", token="wrong-token-on-purpose"
    ),
    policy=policy,
)
print("STATE=" + result.inference_state.value)
"""


def test_fault_injection_fabricating_a_key_on_secret_denial_is_caught() -> None:
    """Mutates the real, on-disk `connection_validation.py` so a denied
    secret-backend token silently fabricates a credential string instead
    of returning `SECRET_ACCESS_DENIED`, points the mutated module at a
    real reachable gateway that accepts that exact fabricated string as a
    valid bearer token, runs it in a fresh subprocess, and proves the
    vulnerability genuinely manifests as a fabricated `connected` result
    without the real `SecretAccessDeniedError` handling -- not merely
    that the mutation was syntactically applied. Restores the file
    byte-identical afterward, whether or not the probe succeeded."""
    if not _UPDATE_CA_AVAILABLE:
        return
    handler = _make_gateway_handler(valid_token=_FABRICATED_KEY)
    server = _start_https_server(handler, _TRUSTED_LEAF, _TRUSTED_KEY)
    port = server.server_address[1]

    # Sanity: the *real*, unmutated module must NOT report connected here --
    # secret resolution genuinely fails first (SECRET_ACCESS_DENIED), the
    # gateway is never actually called with the wrong-token path.
    baseline = validate_connection(
        base_url=f"https://127.0.0.1:{port}",
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong-token-on-purpose"),
        policy=_policy_for(port),
    )
    check(
        "FTF-0203",
        "baseline (unmutated): a denied secret backend token never reaches the "
        "gateway and never reports connected",
        baseline.inference_state == InferenceCheckState.SECRET_ACCESS_DENIED,
    )

    original = _MODULE_PATH.read_bytes()
    try:
        text = original.decode("utf-8")
        # Matches the `except SecretAccessDeniedError:` clause's own body,
        # up to (not including) the next `except` clause -- independent of
        # exact line-wrapping, so a future `ruff format` reflow cannot
        # silently make this fault-injection proof a no-op.
        pattern = re.compile(
            r"except SecretAccessDeniedError:\n(?:.*\n)+?    except SecretExpiredError:"
        )
        match = pattern.search(text)
        check(
            "FTF-0203",
            "the fault-injection target clause is present in the tracked module",
            match is not None,
        )
        mutated = pattern.sub(
            f'except SecretAccessDeniedError:\n        return {_FABRICATED_KEY!r}, None, ""\n'
            "    except SecretExpiredError:",
            text,
        )
        check("FTF-0203", "the mutation actually changed the file", mutated != text)
        _MODULE_PATH.write_text(mutated, encoding="utf-8")

        probe = _FAULT_INJECTION_PROBE.format(root=str(ROOT), port=port, secret_port=_SECRET_PORT)
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30
        )
        check(
            "FTF-0203",
            "fault injection: removing the SecretAccessDeniedError handling genuinely "
            f"lets a denied secret become a fabricated 'connected' result against a real "
            f"gateway (stdout={result.stdout!r} stderr={result.stderr!r})",
            result.returncode == 0 and "STATE=connected" in result.stdout,
        )
    finally:
        _MODULE_PATH.write_bytes(original)
        restored = _MODULE_PATH.read_bytes()
        check(
            "FTF-0203",
            "the module file is restored byte-identical after fault injection",
            restored == original,
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0203 connection validation: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0203 connection validation: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
