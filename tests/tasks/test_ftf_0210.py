#!/usr/bin/env python3
"""FTF-0210 -- Real chat probe.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern every sibling
FTF-020x suite (already CERTIFIED: FTF-0203/0204/0205/0206/0207/0208/0209)
already established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (the same KV v2 read shape `packages/litellm_client/
    secret_manager_integration.py` (FTF-0202) actually calls, exercised
    unmodified);
  - a real local HTTPS server standing in for the organization's LiteLLM
    gateway's own `POST /v1/chat/completions` route, with a real
    self-signed CA this suite generates and installs into the sandbox's
    own system trust store for the duration of the run (removed again in
    `atexit`), so the "probed" happy path exercises real, unmodified TLS
    certificate validation and a real HTTP round trip.

This task's own negative_acceptance ("Sensitive content, unsupported
parameters and permission/timeout errors are normalized without
fabricated responses") is covered by four dedicated real round-trip
checks: `test_sensitive_content_is_normalized_not_fabricated`,
`test_unsupported_parameter_is_normalized_not_fabricated`,
`test_permission_denied_normalizes_without_fabricated_response` and
`test_timeout_is_normalized_not_fabricated`. This task's own acceptance
clause ("Explicitly authorized synthetic probe calls real model and
records actual provider route, usage and latency") is covered by
`test_probe_succeeds_and_records_route_usage_latency` (the real round
trip) and `test_probe_not_authorized_raises_before_any_network_call` (the
authorization gate).

Run directly: python3 tests/tasks/test_ftf_0210.py
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.real_chat_probe import (  # noqa: E402
    ChatProbeNotAuthorizedError,
    ChatProbeState,
    RouteIdentityState,
    _classify_guard_failure,
    _classify_rejection,
    _parse_success_body,
    _parse_usage,
    probe_chat,
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
_SECRET_VALUE = "sk-real-inference-key-0210"
_SECRET_TOKEN = "test-vault-token"

# A second, real, live-resolvable secret whose value the gateway itself
# never accepts -- proves a real gateway-level 401 (INVALID_TOKEN),
# distinct from a secret-backend-level failure (SECRET_NOT_FOUND/
# SECRET_ACCESS_DENIED), which never even reaches the gateway.
_REVOKED_SECRET_PATH = "litellm/org2"
_REVOKED_SECRET_VALUE = "sk-stale-revoked-key-0210"


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
        elif self.path == f"/v1/{_SECRET_MOUNT}/data/{_REVOKED_SECRET_PATH}":
            self._json(
                200,
                {
                    "data": {
                        "data": {_SECRET_FIELD: _REVOKED_SECRET_VALUE},
                        "metadata": {"version": 1},
                    }
                },
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


# -- Real local TLS fixture: same convention every sibling FTF-020x suite --

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0210-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0210-test-ca.crt")
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
            "/CN=FTF-0210 Test CA",
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
        "FTF-0210: this sandbox has no openssl/update-ca-certificates -- the "
        "live TLS-backed checks are skipped and disclosed here; the pure "
        "classifier/parser unit checks still run and still count. GitHub "
        "Actions' ubuntu-latest runner has both tools."
    )


def _policy_for(port: int) -> ConnectionPolicy:
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


@dataclass
class _ChatGatewayState:
    status: int
    body: bytes
    delay_seconds: float
    last_request_body: bytes | None = None


_CHAT_STATE = _ChatGatewayState(
    status=200,
    body=(
        b'{"id":"chatcmpl-real","model":"gpt-4o-mini-2024-07-18",'
        b'"choices":[{"index":0,"message":{"role":"assistant","content":"pong"}}],'
        b'"usage":{"prompt_tokens":9,"completion_tokens":1,"total_tokens":10}}'
    ),
    delay_seconds=0.0,
)


class _ChatGatewayHandler(BaseHTTPRequestHandler):
    """A real HTTPS gateway whose `/v1/chat/completions` response can be
    swapped mid-run (module-level mutable `_CHAT_STATE`) -- used to prove
    every classified outcome against one real, live server rather than a
    stub per test."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        _CHAT_STATE.last_request_body = raw
        if self.path != "/v1/chat/completions":
            self._respond(404, b"")
            return
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {_SECRET_VALUE}":
            self._respond(401, b'{"error":{"type":"invalid_request_error","message":"no key"}}')
            return
        if _CHAT_STATE.delay_seconds:
            time.sleep(_CHAT_STATE.delay_seconds)
        self._respond(_CHAT_STATE.status, _CHAT_STATE.body)


def _reset_gateway() -> None:
    _CHAT_STATE.status = 200
    _CHAT_STATE.body = (
        b'{"id":"chatcmpl-real","model":"gpt-4o-mini-2024-07-18",'
        b'"choices":[{"index":0,"message":{"role":"assistant","content":"pong"}}],'
        b'"usage":{"prompt_tokens":9,"completion_tokens":1,"total_tokens":10}}'
    )
    _CHAT_STATE.delay_seconds = 0.0
    _CHAT_STATE.last_request_body = None


def _probe(port: int, *, model_id: str = "org-agent-small", timeout: float = 10.0):
    return probe_chat(
        base_url=f"https://127.0.0.1:{port}",
        model_id=model_id,
        approved_model_ids=frozenset({model_id}),
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        timeout=timeout,
    )


# -- Tests ----------------------------------------------------------------


def test_probe_succeeds_and_records_route_usage_latency() -> None:
    """This task's own acceptance clause: "Explicitly authorized synthetic
    probe calls real model and records actual provider route, usage and
    latency." The gateway's real `model` field (`gpt-4o-mini-2024-07-18`)
    differs from the requested alias (`org-agent-small`) -- proving this
    is the real resolved route, not an echo of the request."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210", "a real successful probe reports PROBED", result.state == ChatProbeState.PROBED
    )
    check(
        "FTF-0210",
        "the real resolved provider route is recorded and differs from the requested alias",
        result.resolved_model == "gpt-4o-mini-2024-07-18",
    )
    check(
        "FTF-0210",
        "route identity is VERIFIED when the gateway's response names a real model",
        result.route_identity_state == RouteIdentityState.VERIFIED,
    )
    check(
        "FTF-0210",
        "real usage is recorded from the gateway's own response",
        result.usage is not None
        and result.usage.prompt_tokens == 9
        and result.usage.completion_tokens == 1
        and result.usage.total_tokens == 10,
    )
    check(
        "FTF-0210",
        "a real measured latency is recorded",
        result.latency_ms is not None and result.latency_ms >= 0,
    )
    check(
        "FTF-0210",
        "probed_at is a real timezone-aware timestamp",
        result.probed_at.tzinfo is not None,
    )
    check(
        "FTF-0210",
        "the detail string is content-free (no secret, no prompt, no raw answer)",
        _SECRET_VALUE not in result.detail and "pong" not in result.detail,
    )


def test_probe_sends_a_bounded_synthetic_request() -> None:
    """Section 3.4: "using synthetic non-sensitive content and an approved
    probe budget." Observed over the real wire, not just inspected in
    source -- the actual bytes this module sent to a real server."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    _probe(port)
    assert _CHAT_STATE.last_request_body is not None
    sent = json.loads(_CHAT_STATE.last_request_body)
    check(
        "FTF-0210",
        "the real request used the fixed synthetic probe prompt, not caller-supplied content",
        sent["messages"] == [{"role": "user", "content": "Reply with the single word: pong."}],
    )
    check(
        "FTF-0210",
        "the real request carried a bounded max_tokens ceiling",
        isinstance(sent.get("max_tokens"), int) and sent["max_tokens"] <= 16,
    )
    check("FTF-0210", "the real request did not request streaming", sent.get("stream") is False)


def test_missing_usage_is_unknown_not_zero() -> None:
    """Section 3.4: "Missing usage becomes unpriced/unknown, not zero
    cost." A real response with no usage object at all must never produce
    a zero-filled `ChatUsage`."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = (
        b'{"id":"chatcmpl-real","model":"gpt-4o-mini-2024-07-18",'
        b'"choices":[{"index":0,"message":{"role":"assistant","content":"pong"}}]}'
    )
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210",
        "a probe with no usage object still reports PROBED",
        result.state == ChatProbeState.PROBED,
    )
    check("FTF-0210", "missing usage is None, never a zero-filled ChatUsage", result.usage is None)


def test_missing_model_field_is_route_unverified() -> None:
    """Section 3.5: "If identity cannot be resolved, mark
    IDENTITY_UNVERIFIED." A real 200 response with no `model` field must
    never fabricate a resolved route."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = (
        b'{"id":"chatcmpl-real",'
        b'"choices":[{"index":0,"message":{"role":"assistant","content":"pong"}}],'
        b'"usage":{"prompt_tokens":9,"completion_tokens":1,"total_tokens":10}}'
    )
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210",
        "a probe with no model field still reports PROBED",
        result.state == ChatProbeState.PROBED,
    )
    check(
        "FTF-0210",
        "route identity is UNVERIFIED, never fabricated, when no model field is present",
        result.route_identity_state == RouteIdentityState.UNVERIFIED
        and result.resolved_model is None,
    )


def test_secret_not_found_normalizes_without_fabricated_response() -> None:
    """An unresolvable secret reference must never reach the gateway as a
    fabricated success -- it fails closed before any HTTP call, same
    convention `connection_validation.py`/`v1_models_discovery.py` already
    established for this exact failure class."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_chat(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref("wrong-path-so-the-vault-returns-404"),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0210",
        "an unresolvable secret reference never reaches the gateway as a fabricated success",
        result.state == ChatProbeState.SECRET_NOT_FOUND,
    )


def test_gateway_permission_denied_is_invalid_token() -> None:
    """This task's own negative_acceptance: "...permission... errors are
    normalized without fabricated responses." A real 401/403 from the
    gateway itself (as opposed to a secret-backend-level failure above)
    must never be reported as PROBED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_chat(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong-token"),
        policy=_policy_for(port),
    )
    check(
        "FTF-0210",
        "a secret backend that denies access never reaches the gateway as a fabricated success",
        result.state == ChatProbeState.SECRET_ACCESS_DENIED,
    )

    revoked_result = probe_chat(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref(_REVOKED_SECRET_PATH),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0210",
        "a real gateway 401 on a resolved-but-stale credential is INVALID_TOKEN, a real "
        "round trip distinct from a secret-backend-level failure",
        revoked_result.state == ChatProbeState.INVALID_TOKEN,
    )
    check(
        "FTF-0210",
        "a real gateway rejection never carries usage or a resolved model",
        revoked_result.usage is None and revoked_result.resolved_model is None,
    )

    real_gateway_result = _probe(port)
    check(
        "FTF-0210",
        "sanity: the real gateway accepts the real secret and reports PROBED",
        real_gateway_result.state == ChatProbeState.PROBED,
    )


def test_sensitive_content_is_normalized_not_fabricated() -> None:
    """This task's own negative_acceptance: "Sensitive content... errors
    are normalized without fabricated responses." A real 400 with an
    OpenAI-compatible content-filter error must be CONTENT_FILTERED, never
    a fabricated PROBED result."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.status = 400
    _CHAT_STATE.body = json.dumps(
        {"error": {"type": "content_filter", "code": "content_filter", "message": "blocked"}}
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210",
        "a real content-policy rejection is CONTENT_FILTERED, never PROBED",
        result.state == ChatProbeState.CONTENT_FILTERED,
    )
    check(
        "FTF-0210",
        "a rejection never carries usage or a resolved model",
        result.usage is None and result.resolved_model is None,
    )
    check(
        "FTF-0210",
        "the detail string never echoes the raw provider error message",
        "blocked" not in result.detail,
    )


def test_unsupported_parameter_is_normalized_not_fabricated() -> None:
    """This task's own negative_acceptance: "...unsupported parameters...
    are normalized without fabricated responses." A real 400 naming an
    unsupported request parameter must be UNSUPPORTED_PARAMETER, distinct
    from CONTENT_FILTERED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.status = 400
    _CHAT_STATE.body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "unsupported_parameter",
                "param": "max_tokens",
                "message": "this model does not support max_tokens",
            }
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210",
        "a real unsupported-parameter rejection is UNSUPPORTED_PARAMETER, distinct from "
        "content filtering",
        result.state == ChatProbeState.UNSUPPORTED_PARAMETER,
    )


def test_unrecognized_rejection_is_malformed_not_guessed() -> None:
    """A real 400 that matches neither known rejection taxonomy must never
    be guessed into CONTENT_FILTERED or UNSUPPORTED_PARAMETER."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.status = 400
    _CHAT_STATE.body = json.dumps(
        {"error": {"type": "server_error", "message": "something else entirely"}}
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210",
        "an unrecognized real 400 is MALFORMED_RESPONSE, never guessed into a named category",
        result.state == ChatProbeState.MALFORMED_RESPONSE,
    )


def test_timeout_is_normalized_not_fabricated() -> None:
    """This task's own negative_acceptance: "...timeout errors are
    normalized without fabricated responses." A real server that
    deliberately outlives the client's own bounded timeout must produce a
    real TIMEOUT, never a hang or a fabricated success."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.delay_seconds = 2.0
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    started = time.monotonic()
    result = _probe(port, timeout=0.3)
    elapsed = time.monotonic() - started
    check(
        "FTF-0210",
        "a real connection-level timeout is reported as TIMEOUT, never a hang",
        result.state == ChatProbeState.TIMEOUT,
    )
    check(
        "FTF-0210",
        "the call actually returned near the bounded timeout, not after the server's full delay",
        elapsed < 1.5,
    )


def test_malformed_success_body_never_fabricates_a_completion() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = b'{"id":"chatcmpl-real","model":"gpt-4o-mini"}'
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0210",
        "a 200 response with no choices is MALFORMED_RESPONSE, never fabricated into PROBED",
        result.state == ChatProbeState.MALFORMED_RESPONSE,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    """A policy that never explicitly allowlists the loopback range must
    deny a real 127.0.0.1 destination before any connection is attempted
    -- Section 65.4's "internal org endpoints are separately allowlisted,
    not blanket-blocked," proven against the real running gateway rather
    than an address that is merely unreachable."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_chat(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=ConnectionPolicy(
            name="test-litellm-gateway-no-allowlist",
            allowed_schemes=frozenset({"https"}),
            allowed_ports=frozenset({port}),
        ),
    )
    check(
        "FTF-0210",
        "a real, running gateway on an un-allowlisted loopback address is denied pre-connect",
        result.state == ChatProbeState.DESTINATION_DENIED,
    )


def test_probe_not_authorized_raises_before_any_network_call() -> None:
    """This task's own acceptance clause: "Explicitly authorized synthetic
    probe...". `model_id` not present in `approved_model_ids` must raise
    before any network attempt -- proven by pointing at a port nothing
    listens on: if the authorization gate did not run first, this would
    return a `ChatProbeResult` (a real connection failure), not raise."""
    raised = False
    try:
        probe_chat(
            base_url="https://127.0.0.1:1",
            model_id="unapproved-model",
            approved_model_ids=frozenset({"org-agent-small"}),
            secret_ref=_secret_ref(),
            secret_config=_SECRET_CONFIG,
            policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
        )
    except ChatProbeNotAuthorizedError as exc:
        raised = True
        check(
            "FTF-0210",
            "the authorization error names the unapproved model, content-free otherwise",
            "unapproved-model" in str(exc),
        )
    check(
        "FTF-0210",
        "an unapproved model raises ChatProbeNotAuthorizedError instead of attempting a probe",
        raised,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    unsupported = probe_chat(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://secret/data/x#field",
        secret_config=_SECRET_CONFIG,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0210",
        "an unsupported secret backend scheme is a real, distinct state",
        unsupported.state == ChatProbeState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = probe_chat(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0210",
        "a missing secret backend configuration is a real, distinct state",
        no_config.state == ChatProbeState.SECRET_BACKEND_ERROR,
    )
    check(
        "FTF-0210",
        "neither secret failure ever carries a resolved route or usage",
        unsupported.resolved_model is None
        and unsupported.usage is None
        and no_config.resolved_model is None
        and no_config.usage is None,
    )


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0210",
        "DNS resolution failure classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == ChatProbeState.DNS_FAILED,
    )
    check(
        "FTF-0210",
        "certificate verification failure classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == ChatProbeState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0210",
        "a bare TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == ChatProbeState.TIMEOUT,
    )
    check(
        "FTF-0210",
        "a refused connection classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == ChatProbeState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0210",
        "an unrecognized OSError falls back to unreachable, never invented as success",
        _classify_guard_failure(_wrapped(OSError("something else"))) == ChatProbeState.UNREACHABLE,
    )


def test_classify_rejection_is_pure_and_never_guesses() -> None:
    check(
        "FTF-0210",
        "invalid JSON is malformed_response",
        _classify_rejection(b"not json")[0] == ChatProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0210",
        "JSON with no error object is malformed_response",
        _classify_rejection(b'{"message":"nope"}')[0] == ChatProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0210",
        "a content_policy_violation code classifies as content_filtered",
        _classify_rejection(json.dumps({"error": {"code": "content_policy_violation"}}).encode())[0]
        == ChatProbeState.CONTENT_FILTERED,
    )
    check(
        "FTF-0210",
        "a named param classifies as unsupported_parameter even without a matching code",
        _classify_rejection(
            json.dumps({"error": {"type": "invalid_request_error", "param": "tools"}}).encode()
        )[0]
        == ChatProbeState.UNSUPPORTED_PARAMETER,
    )


def test_parse_usage_rejects_non_int_and_bool_fields() -> None:
    check("FTF-0210", "a non-dict usage payload parses to None", _parse_usage("nope") is None)
    check(
        "FTF-0210",
        "a usage payload with a string field parses to None, never partially filled",
        _parse_usage({"prompt_tokens": "9", "completion_tokens": 1, "total_tokens": 10}) is None,
    )
    check(
        "FTF-0210",
        "a usage payload with a bool field (an int subclass) parses to None",
        _parse_usage({"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2}) is None,
    )
    usage = _parse_usage({"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10})
    check(
        "FTF-0210",
        "a well-formed usage payload parses to the real observed values",
        usage is not None
        and usage.prompt_tokens == 9
        and usage.completion_tokens == 1
        and usage.total_tokens == 10,
    )


def test_parse_success_body_requires_real_choices() -> None:
    check(
        "FTF-0210",
        "a response with an empty choices list is never treated as a real completion",
        _parse_success_body(json.dumps({"model": "m", "choices": []}).encode()) is None,
    )
    check(
        "FTF-0210",
        "a response with no choices key at all is never treated as a real completion",
        _parse_success_body(json.dumps({"model": "m"}).encode()) is None,
    )
    parsed = _parse_success_body(json.dumps({"model": "m", "choices": [{"index": 0}]}).encode())
    check("FTF-0210", "a real minimal completion parses its model field", parsed == ("m", None))


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0210 real chat probe: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0210 real chat probe: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
