#!/usr/bin/env python3
"""FTF-0212 -- Real tool-call probe.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern every sibling
FTF-020x suite (already CERTIFIED: FTF-0203/.../FTF-0210/FTF-0211) already
established:

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

This task's own negative_acceptance ("An ordinary textual JSON answer is
not proof of a tool-call capability; invalid tool result is failed
probe") is covered by four dedicated real round-trip checks:
`test_no_tool_call_issued_is_not_proof`,
`test_textual_json_mimicking_tool_call_is_not_proof`,
`test_tool_name_mismatch_is_failed_probe` and
`test_invalid_tool_arguments_is_failed_probe`. This task's own acceptance
clause ("Probe tool schema/name/arguments using non-sensitive case and
record advertised versus observed result") is covered by
`test_probe_succeeds_and_records_advertised_vs_observed` (the real round
trip) and `test_probe_sends_a_bounded_synthetic_tool_request` (the real
request bytes) plus `test_probe_not_authorized_raises_before_any_network_call`
(the authorization gate, reused from FTF-0210's own contract).

Run directly: python3 tests/tasks/test_ftf_0212.py
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

from packages.litellm_client.real_tool_call_probe import (  # noqa: E402
    ChatProbeNotAuthorizedError,
    ToolCallProbeState,
    _arguments_are_structurally_valid,
    _classify_guard_failure,
    _classify_rejection,
    _extract_first_tool_call,
    _parse_success_body,
    probe_tool_call,
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
_SECRET_VALUE = "sk-real-inference-key-0212"
_SECRET_TOKEN = "test-vault-token"

_REVOKED_SECRET_PATH = "litellm/org2"
_REVOKED_SECRET_VALUE = "sk-stale-revoked-key-0212"


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

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0212-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0212-test-ca.crt")
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
            "/CN=FTF-0212 Test CA",
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
        "FTF-0212: this sandbox has no openssl/update-ca-certificates -- the "
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


def _tool_call_body(
    *,
    model: str = "gpt-4o-mini-2024-07-18",
    name: str = "get_probe_time",
    arguments: str = '{"timezone": "UTC"}',
) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-real",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
        }
    ).encode("utf-8")


@dataclass
class _ChatGatewayState:
    status: int
    body: bytes
    delay_seconds: float
    last_request_body: bytes | None = None


_CHAT_STATE = _ChatGatewayState(status=200, body=_tool_call_body(), delay_seconds=0.0)


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
    _CHAT_STATE.body = _tool_call_body()
    _CHAT_STATE.delay_seconds = 0.0
    _CHAT_STATE.last_request_body = None


def _probe(port: int, *, model_id: str = "org-agent-small", timeout: float = 10.0):
    return probe_tool_call(
        base_url=f"https://127.0.0.1:{port}",
        model_id=model_id,
        approved_model_ids=frozenset({model_id}),
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        timeout=timeout,
    )


# -- Tests ----------------------------------------------------------------


def test_probe_succeeds_and_records_advertised_vs_observed() -> None:
    """This task's own acceptance clause 1: "...record advertised versus
    observed result." The gateway's real `model` field differs from the
    requested alias, proving this is the real resolved route."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "a real tool invocation matching the advertised schema reports PROBED",
        result.state == ToolCallProbeState.PROBED,
    )
    check(
        "FTF-0212",
        "the advertised tool name is always the fixed probe constant",
        result.advertised_tool_name == "get_probe_time",
    )
    check(
        "FTF-0212",
        "the observed tool name is the real value the gateway returned",
        result.observed_tool_name == "get_probe_time",
    )
    check(
        "FTF-0212",
        "the observed arguments are the real raw JSON string the gateway returned",
        result.observed_arguments == '{"timezone": "UTC"}',
    )
    check(
        "FTF-0212",
        "the real resolved provider route is recorded and differs from the requested alias",
        result.resolved_model == "gpt-4o-mini-2024-07-18",
    )
    check(
        "FTF-0212",
        "real usage is recorded from the gateway's own response",
        result.usage is not None
        and result.usage.prompt_tokens == 20
        and result.usage.completion_tokens == 6
        and result.usage.total_tokens == 26,
    )
    check(
        "FTF-0212",
        "a real measured latency is recorded",
        result.latency_ms is not None and result.latency_ms >= 0,
    )
    check(
        "FTF-0212",
        "the detail string is content-free (no secret, no raw arguments)",
        _SECRET_VALUE not in result.detail and "UTC" not in result.detail,
    )


def test_probe_sends_a_bounded_synthetic_tool_request() -> None:
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
        "FTF-0212",
        "the real request used the fixed synthetic probe prompt, not caller-supplied content",
        sent["messages"]
        == [{"role": "user", "content": "Call the get_probe_time tool with timezone set to UTC."}],
    )
    check(
        "FTF-0212",
        "the real request carried a bounded max_tokens ceiling",
        isinstance(sent.get("max_tokens"), int) and sent["max_tokens"] <= 64,
    )
    check("FTF-0212", "the real request did not request streaming", sent.get("stream") is False)
    check(
        "FTF-0212",
        "the real request advertised exactly one fixed tool",
        isinstance(sent.get("tools"), list)
        and len(sent["tools"]) == 1
        and sent["tools"][0]["function"]["name"] == "get_probe_time",
    )
    check(
        "FTF-0212",
        "the real request forced the advertised tool via tool_choice, not free-form auto",
        sent.get("tool_choice") == {"type": "function", "function": {"name": "get_probe_time"}},
    )


def test_no_tool_call_issued_is_not_proof() -> None:
    """This task's own negative_acceptance: "An ordinary textual JSON
    answer is not proof of a tool-call capability." A real 200 response
    whose message carries ordinary text and no `tool_calls` field at all
    must never be reported as PROBED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = json.dumps(
        {
            "id": "chatcmpl-real",
            "model": "gpt-4o-mini-2024-07-18",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Sure, it's UTC now."}}
            ],
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "a real ordinary text answer with no tool_calls field is NO_TOOL_CALL_ISSUED, never PROBED",
        result.state == ToolCallProbeState.NO_TOOL_CALL_ISSUED,
    )
    check(
        "FTF-0212",
        "no observed tool name/arguments are fabricated when none were issued",
        result.observed_tool_name is None and result.observed_arguments is None,
    )


def test_textual_json_mimicking_tool_call_is_not_proof() -> None:
    """Same negative_acceptance clause, the sharper case: the message's
    real `content` field contains text that *looks* like a tool
    invocation, but there is still no real `tool_calls` field. This module
    must never read `content` to manufacture a tool call."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = json.dumps(
        {
            "id": "chatcmpl-real",
            "model": "gpt-4o-mini-2024-07-18",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"name": "get_probe_time", "arguments": {"timezone": "UTC"}}',
                    },
                }
            ],
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "tool-shaped JSON typed as plain text content is never treated as a real tool call",
        result.state == ToolCallProbeState.NO_TOOL_CALL_ISSUED,
    )


def test_tool_name_mismatch_is_failed_probe() -> None:
    """This task's own negative_acceptance: "...invalid tool result is
    failed probe." A real tool call naming a different function than the
    one advertised must never be accepted as proof."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _tool_call_body(name="unexpected_tool")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "a real tool call naming a different function is TOOL_NAME_MISMATCH, never PROBED",
        result.state == ToolCallProbeState.TOOL_NAME_MISMATCH,
    )
    check(
        "FTF-0212",
        "the real observed (mismatched) name is still recorded, not hidden",
        result.observed_tool_name == "unexpected_tool",
    )


def test_invalid_tool_arguments_is_failed_probe() -> None:
    """Same negative_acceptance clause: the right tool name with malformed
    or schema-incomplete arguments is still a failed probe, not PROBED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _tool_call_body(arguments="not valid json")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    malformed_result = _probe(port)
    check(
        "FTF-0212",
        "non-JSON arguments on the right tool name is INVALID_TOOL_ARGUMENTS, never PROBED",
        malformed_result.state == ToolCallProbeState.INVALID_TOOL_ARGUMENTS,
    )

    _CHAT_STATE.body = _tool_call_body(arguments="{}")
    server2 = _start_https_server(_ChatGatewayHandler)
    port2 = server2.server_address[1]
    missing_field_result = _probe(port2)
    check(
        "FTF-0212",
        "valid JSON missing the schema's one required property is also INVALID_TOOL_ARGUMENTS",
        missing_field_result.state == ToolCallProbeState.INVALID_TOOL_ARGUMENTS,
    )


def test_missing_usage_is_unknown_not_zero() -> None:
    """Section 3.4: "Missing usage becomes unpriced/unknown, not zero
    cost." A real response with no usage object at all must never produce
    a zero-filled `ChatUsage`."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    body = json.loads(_tool_call_body())
    del body["usage"]
    _CHAT_STATE.body = json.dumps(body).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "a probe with no usage object still reports PROBED",
        result.state == ToolCallProbeState.PROBED,
    )
    check("FTF-0212", "missing usage is None, never a zero-filled ChatUsage", result.usage is None)


def test_missing_model_field_is_route_unverified() -> None:
    """Section 3.5: "If identity cannot be resolved, mark
    IDENTITY_UNVERIFIED." A real 200 response with no `model` field must
    never fabricate a resolved route."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    body = json.loads(_tool_call_body())
    del body["model"]
    _CHAT_STATE.body = json.dumps(body).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "a probe with no model field still reports PROBED",
        result.state == ToolCallProbeState.PROBED,
    )
    check(
        "FTF-0212", "resolved model is never fabricated when absent", result.resolved_model is None
    )


def test_secret_not_found_normalizes_without_fabricated_response() -> None:
    """An unresolvable secret reference must never reach the gateway as a
    fabricated success -- it fails closed before any HTTP call, same
    convention every sibling FTF-020x module already established."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_tool_call(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref("wrong-path-so-the-vault-returns-404"),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0212",
        "an unresolvable secret reference never reaches the gateway as a fabricated success",
        result.state == ToolCallProbeState.SECRET_NOT_FOUND,
    )


def test_gateway_permission_denied_is_invalid_token() -> None:
    """A real 401/403 from the gateway itself (as opposed to a
    secret-backend-level failure) must never be reported as PROBED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]

    revoked_result = probe_tool_call(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref(_REVOKED_SECRET_PATH),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0212",
        "a real gateway 401 on a resolved-but-stale credential is INVALID_TOKEN, a real "
        "round trip distinct from a secret-backend-level failure",
        revoked_result.state == ToolCallProbeState.INVALID_TOKEN,
    )
    check(
        "FTF-0212",
        "a real gateway rejection never carries usage, a resolved model or observed tool data",
        revoked_result.usage is None
        and revoked_result.resolved_model is None
        and revoked_result.observed_tool_name is None,
    )

    real_gateway_result = _probe(port)
    check(
        "FTF-0212",
        "sanity: the real gateway accepts the real secret and reports PROBED",
        real_gateway_result.state == ToolCallProbeState.PROBED,
    )


def test_sensitive_content_is_normalized_not_fabricated() -> None:
    """A real 400 with an OpenAI-compatible content-filter error must be
    CONTENT_FILTERED, never a fabricated PROBED result."""
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
        "FTF-0212",
        "a real content-policy rejection is CONTENT_FILTERED, never PROBED",
        result.state == ToolCallProbeState.CONTENT_FILTERED,
    )
    check(
        "FTF-0212",
        "the detail string never echoes the raw provider error message",
        "blocked" not in result.detail,
    )


def test_unsupported_parameter_is_normalized_not_fabricated() -> None:
    """A real 400 naming an unsupported request parameter (e.g. a gateway
    that does not support `tools` for this model) must be
    UNSUPPORTED_PARAMETER, distinct from CONTENT_FILTERED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.status = 400
    _CHAT_STATE.body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "unsupported_parameter",
                "param": "tools",
                "message": "this model does not support tools",
            }
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0212",
        "a real unsupported-parameter rejection is UNSUPPORTED_PARAMETER, distinct from "
        "content filtering",
        result.state == ToolCallProbeState.UNSUPPORTED_PARAMETER,
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
        "FTF-0212",
        "an unrecognized real 400 is MALFORMED_RESPONSE, never guessed into a named category",
        result.state == ToolCallProbeState.MALFORMED_RESPONSE,
    )


def test_timeout_is_normalized_not_fabricated() -> None:
    """A real server that deliberately outlives the client's own bounded
    timeout must produce a real TIMEOUT, never a hang or a fabricated
    success."""
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
        "FTF-0212",
        "a real connection-level timeout is reported as TIMEOUT, never a hang",
        result.state == ToolCallProbeState.TIMEOUT,
    )
    check(
        "FTF-0212",
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
        "FTF-0212",
        "a 200 response with no choices is MALFORMED_RESPONSE, never fabricated into PROBED",
        result.state == ToolCallProbeState.MALFORMED_RESPONSE,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    """A policy that never explicitly allowlists the loopback range must
    deny a real 127.0.0.1 destination before any connection is attempted,
    proven against the real running gateway rather than an address that
    is merely unreachable."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_tool_call(
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
        "FTF-0212",
        "a real, running gateway on an un-allowlisted loopback address is denied pre-connect",
        result.state == ToolCallProbeState.DESTINATION_DENIED,
    )


def test_probe_not_authorized_raises_before_any_network_call() -> None:
    """This task's own acceptance clause (reusing FTF-0210's exact
    authorization contract): `model_id` not present in
    `approved_model_ids` must raise before any network attempt -- proven
    by pointing at a port nothing listens on: if the authorization gate
    did not run first, this would return a `ToolCallProbeResult` (a real
    connection failure), not raise."""
    raised = False
    try:
        probe_tool_call(
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
            "FTF-0212",
            "the authorization error names the unapproved model, content-free otherwise",
            "unapproved-model" in str(exc),
        )
    check(
        "FTF-0212",
        "an unapproved model raises ChatProbeNotAuthorizedError instead of attempting a probe",
        raised,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    unsupported = probe_tool_call(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://secret/data/x#field",
        secret_config=_SECRET_CONFIG,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0212",
        "an unsupported secret backend scheme is a real, distinct state",
        unsupported.state == ToolCallProbeState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = probe_tool_call(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0212",
        "a missing secret backend configuration is a real, distinct state",
        no_config.state == ToolCallProbeState.SECRET_BACKEND_ERROR,
    )
    check(
        "FTF-0212",
        "neither secret failure ever carries a resolved route, usage or observed tool data",
        unsupported.resolved_model is None
        and unsupported.usage is None
        and unsupported.observed_tool_name is None
        and no_config.resolved_model is None
        and no_config.usage is None
        and no_config.observed_tool_name is None,
    )


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0212",
        "DNS resolution failure classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == ToolCallProbeState.DNS_FAILED,
    )
    check(
        "FTF-0212",
        "certificate verification failure classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == ToolCallProbeState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0212",
        "a bare TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == ToolCallProbeState.TIMEOUT,
    )
    check(
        "FTF-0212",
        "a refused connection classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == ToolCallProbeState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0212",
        "an unrecognized OSError falls back to unreachable, never invented as success",
        _classify_guard_failure(_wrapped(OSError("something else")))
        == ToolCallProbeState.UNREACHABLE,
    )


def test_classify_rejection_is_pure_and_never_guesses() -> None:
    check(
        "FTF-0212",
        "invalid JSON is malformed_response",
        _classify_rejection(b"not json")[0] == ToolCallProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0212",
        "JSON with no error object is malformed_response",
        _classify_rejection(b'{"message":"nope"}')[0] == ToolCallProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0212",
        "a content_policy_violation code classifies as content_filtered",
        _classify_rejection(json.dumps({"error": {"code": "content_policy_violation"}}).encode())[0]
        == ToolCallProbeState.CONTENT_FILTERED,
    )
    check(
        "FTF-0212",
        "a named param classifies as unsupported_parameter even without a matching code",
        _classify_rejection(
            json.dumps({"error": {"type": "invalid_request_error", "param": "tools"}}).encode()
        )[0]
        == ToolCallProbeState.UNSUPPORTED_PARAMETER,
    )


def test_extract_first_tool_call_is_pure_and_never_reads_content() -> None:
    check(
        "FTF-0212",
        "a message with no tool_calls field extracts nothing",
        _extract_first_tool_call({"role": "assistant", "content": "hi"}) is None,
    )
    check(
        "FTF-0212",
        "a message with an empty tool_calls list extracts nothing",
        _extract_first_tool_call({"role": "assistant", "tool_calls": []}) is None,
    )
    check(
        "FTF-0212",
        "a well-formed tool_calls entry extracts its real name and arguments",
        _extract_first_tool_call(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_probe_time", "arguments": '{"timezone":"UTC"}'},
                    }
                ],
            }
        )
        == ("get_probe_time", '{"timezone":"UTC"}'),
    )
    check(
        "FTF-0212",
        "a malformed tool_calls entry (no function field) extracts nothing, never guessed",
        _extract_first_tool_call(
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function"}]}
        )
        is None,
    )


def test_arguments_structural_validity_is_pure() -> None:
    check(
        "FTF-0212",
        "well-formed arguments with the required string property are valid",
        _arguments_are_structurally_valid('{"timezone": "UTC"}') is True,
    )
    check(
        "FTF-0212",
        "non-JSON arguments are invalid",
        _arguments_are_structurally_valid("not json") is False,
    )
    check(
        "FTF-0212",
        "a JSON array (not an object) is invalid",
        _arguments_are_structurally_valid("[1, 2, 3]") is False,
    )
    check(
        "FTF-0212",
        "a JSON object missing the required property is invalid",
        _arguments_are_structurally_valid("{}") is False,
    )
    check(
        "FTF-0212",
        "a JSON object with the required property as the wrong type is invalid",
        _arguments_are_structurally_valid('{"timezone": 5}') is False,
    )
    check(
        "FTF-0212",
        "a JSON object with an empty-string required property is invalid",
        _arguments_are_structurally_valid('{"timezone": ""}') is False,
    )


def test_parse_success_body_requires_a_real_message() -> None:
    check(
        "FTF-0212",
        "a response with an empty choices list is never treated as a real completion",
        _parse_success_body(json.dumps({"model": "m", "choices": []}).encode()) is None,
    )
    check(
        "FTF-0212",
        "a choice with no message object is never treated as a real completion",
        _parse_success_body(json.dumps({"model": "m", "choices": [{"index": 0}]}).encode()) is None,
    )
    parsed = _parse_success_body(
        json.dumps(
            {"model": "m", "choices": [{"index": 0, "message": {"role": "assistant"}}]}
        ).encode()
    )
    check(
        "FTF-0212",
        "a real minimal completion with a message but no tool_calls parses cleanly with no "
        "fabricated tool data",
        parsed == ("m", None, None, None),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0212 real tool-call probe: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0212 real tool-call probe: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
