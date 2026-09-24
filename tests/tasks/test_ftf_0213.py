#!/usr/bin/env python3
"""FTF-0213 -- Real structured-output probe.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern every sibling
FTF-020x suite (already CERTIFIED: FTF-0203/.../FTF-0211) already
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

This task's own acceptance clause ("Exercise the model's advertised
structured-output protocol and schema-validate actual response") is
covered by `test_probe_succeeds_and_records_route_usage_latency` (a real
schema-valid response) and
`test_probe_sends_a_bounded_synthetic_json_schema_request` (the real
`response_format` bytes actually sent over the wire).

This task's own negative_acceptance ("JSON-looking prose, unknown
parameter rejection or coercion is not silently treated as strict schema
support") is covered by three dedicated real round-trip checks:
`test_json_looking_prose_is_not_json`,
`test_unsupported_response_format_parameter_is_not_fabricated` and
`test_coerced_type_is_a_schema_violation_not_probed` (plus
`test_missing_required_field_is_a_schema_violation`,
`test_additional_property_is_a_schema_violation` and
`test_enum_mismatch_is_a_schema_violation` for the rest of the schema
surface).

Run directly: python3 tests/tasks/test_ftf_0213.py
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

from packages.litellm_client.real_chat_probe import ChatProbeNotAuthorizedError  # noqa: E402
from packages.litellm_client.real_structured_output_probe import (  # noqa: E402
    RouteIdentityState,
    StructuredOutputProbeState,
    _classify_guard_failure,
    _classify_rejection,
    _parse_structured_content,
    _validate_against_schema,
    probe_structured_output,
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
_SECRET_VALUE = "sk-real-inference-key-0213"
_SECRET_TOKEN = "test-vault-token"

_REVOKED_SECRET_PATH = "litellm/org2"
_REVOKED_SECRET_VALUE = "sk-stale-revoked-key-0213"


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

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0213-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0213-test-ca.crt")
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
            "/CN=FTF-0213 Test CA",
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
        "FTF-0213: this sandbox has no openssl/update-ca-certificates -- the "
        "live TLS-backed checks are skipped and disclosed here; the pure "
        "classifier/parser/validator unit checks still run and still count. "
        "GitHub Actions' ubuntu-latest runner has both tools."
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


def _success_body(content: str, *, model: str = "gpt-4o-mini-2024-07-18") -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-real",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        }
    ).encode("utf-8")


@dataclass
class _ChatGatewayState:
    status: int
    body: bytes
    delay_seconds: float
    request_count: int = 0
    last_request_body: bytes | None = None


_CHAT_STATE = _ChatGatewayState(
    status=200,
    body=_success_body('{"word": "pong", "count": 1}'),
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
        _CHAT_STATE.request_count += 1
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
    _CHAT_STATE.body = _success_body('{"word": "pong", "count": 1}')
    _CHAT_STATE.delay_seconds = 0.0
    _CHAT_STATE.request_count = 0
    _CHAT_STATE.last_request_body = None


def _probe(port: int, *, model_id: str = "org-agent-small", timeout: float = 10.0):
    return probe_structured_output(
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
    """This task's own acceptance clause: "Exercise the model's advertised
    structured-output protocol and schema-validate actual response." A
    real schema-valid response must be PROBED with the decoded content
    preserved."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a real schema-valid probe reports PROBED",
        result.state == StructuredOutputProbeState.PROBED,
    )
    check(
        "FTF-0213",
        "the real resolved provider route is recorded and differs from the requested alias",
        result.resolved_model == "gpt-4o-mini-2024-07-18",
    )
    check(
        "FTF-0213",
        "route identity is VERIFIED when the gateway's response names a real model",
        result.route_identity_state == RouteIdentityState.VERIFIED,
    )
    check(
        "FTF-0213",
        "the real decoded, schema-valid content is returned",
        result.parsed_content == {"word": "pong", "count": 1},
    )
    check(
        "FTF-0213",
        "real usage is recorded from the gateway's own response",
        result.usage is not None
        and result.usage.prompt_tokens == 12
        and result.usage.completion_tokens == 6,
    )
    check(
        "FTF-0213",
        "a real measured latency is recorded",
        result.latency_ms is not None and result.latency_ms >= 0,
    )
    check(
        "FTF-0213",
        "probed_at is a real timezone-aware timestamp",
        result.probed_at.tzinfo is not None,
    )
    check(
        "FTF-0213",
        "the detail string is content-free (no secret)",
        _SECRET_VALUE not in result.detail,
    )


def test_probe_sends_a_bounded_synthetic_json_schema_request() -> None:
    """Section 3.4: "using synthetic non-sensitive content and an approved
    probe budget," applied to this task's own real
    `response_format: json_schema` request. Observed over the real wire,
    not just inspected in source."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    _probe(port)
    assert _CHAT_STATE.last_request_body is not None
    sent = json.loads(_CHAT_STATE.last_request_body)
    check(
        "FTF-0213",
        "the real request used a fixed synthetic probe prompt, not caller-supplied content",
        isinstance(sent.get("messages"), list)
        and len(sent["messages"]) == 1
        and sent["messages"][0]["role"] == "user",
    )
    check(
        "FTF-0213",
        "the real request carried a bounded max_tokens ceiling",
        isinstance(sent.get("max_tokens"), int) and sent["max_tokens"] <= 64,
    )
    check("FTF-0213", "the real request did not request streaming", sent.get("stream") is False)
    response_format = sent.get("response_format")
    check(
        "FTF-0213",
        "the real request carried a real json_schema response_format",
        isinstance(response_format, dict) and response_format.get("type") == "json_schema",
    )
    json_schema = response_format.get("json_schema") if isinstance(response_format, dict) else None
    check(
        "FTF-0213",
        "the real json_schema payload is strict and carries the actual probe schema",
        isinstance(json_schema, dict)
        and json_schema.get("strict") is True
        and json_schema.get("schema", {}).get("additionalProperties") is False,
    )


def test_json_looking_prose_is_not_json() -> None:
    """This task's own negative_acceptance: "JSON-looking prose... is not
    silently treated as strict schema support." A real response whose
    content merely looks JSON-ish (unquoted keys) must be NOT_JSON, never
    a fabricated PROBED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _success_body("Sure, here you go: {word: pong, count: 1}")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "JSON-looking prose that does not actually parse as JSON is NOT_JSON, never PROBED",
        result.state == StructuredOutputProbeState.NOT_JSON,
    )
    check(
        "FTF-0213", "a NOT_JSON result never carries decoded content", result.parsed_content is None
    )


def test_coerced_type_is_a_schema_violation_not_probed() -> None:
    """This task's own negative_acceptance: "...coercion is not silently
    treated as strict schema support." A real response with `count` sent
    as a numeric-looking string, not a real integer, must be
    SCHEMA_VIOLATION, never coerced into PROBED."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _success_body('{"word": "pong", "count": "1"}')
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a numeric-looking string is never coerced into a passing integer check",
        result.state == StructuredOutputProbeState.SCHEMA_VIOLATION,
    )


def test_missing_required_field_is_a_schema_violation() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _success_body('{"word": "pong"}')
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a real response missing a required schema field is SCHEMA_VIOLATION",
        result.state == StructuredOutputProbeState.SCHEMA_VIOLATION,
    )


def test_additional_property_is_a_schema_violation() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _success_body('{"word": "pong", "count": 1, "extra": true}')
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a real response with an undeclared additional property is SCHEMA_VIOLATION",
        result.state == StructuredOutputProbeState.SCHEMA_VIOLATION,
    )


def test_enum_mismatch_is_a_schema_violation() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _success_body('{"word": "ping", "count": 1}')
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a real response whose value is not in the schema's enum is SCHEMA_VIOLATION",
        result.state == StructuredOutputProbeState.SCHEMA_VIOLATION,
    )


def test_top_level_array_is_a_schema_violation_not_not_json() -> None:
    """Valid JSON that is not the required top-level `object` shape is
    still real, parseable JSON -- SCHEMA_VIOLATION, not NOT_JSON, since
    NOT_JSON is reserved for content that fails to parse at all."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = _success_body('["pong", 1]')
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "valid JSON of the wrong top-level type is SCHEMA_VIOLATION, distinct from NOT_JSON",
        result.state == StructuredOutputProbeState.SCHEMA_VIOLATION,
    )


def test_unsupported_response_format_parameter_is_not_fabricated() -> None:
    """This task's own negative_acceptance: "...unknown parameter
    rejection... is not silently treated as strict schema support." A real
    400 naming `response_format` as unsupported must be
    UNSUPPORTED_PARAMETER, and the probe must never silently retry without
    structured mode and report a fabricated success."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.status = 400
    _CHAT_STATE.body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "unsupported_parameter",
                "param": "response_format",
                "message": "this model does not support response_format",
            }
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a real response_format rejection is UNSUPPORTED_PARAMETER, never fabricated into PROBED",
        result.state == StructuredOutputProbeState.UNSUPPORTED_PARAMETER,
    )
    check(
        "FTF-0213",
        "the probe issues exactly one real request -- it never silently retries without "
        "structured mode",
        _CHAT_STATE.request_count == 1,
    )


def test_content_filtered_is_normalized_not_fabricated() -> None:
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
        "FTF-0213",
        "a real content-policy rejection is CONTENT_FILTERED, never PROBED",
        result.state == StructuredOutputProbeState.CONTENT_FILTERED,
    )


def test_missing_usage_is_unknown_not_zero() -> None:
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
                    "message": {"role": "assistant", "content": '{"word": "pong", "count": 1}'},
                }
            ],
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a probe with no usage object still reports PROBED",
        result.state == StructuredOutputProbeState.PROBED,
    )
    check("FTF-0213", "missing usage is None, never a zero-filled ChatUsage", result.usage is None)


def test_missing_model_field_is_route_unverified() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = json.dumps(
        {
            "id": "chatcmpl-real",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"word": "pong", "count": 1}'},
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a probe with no model field still reports PROBED",
        result.state == StructuredOutputProbeState.PROBED,
    )
    check(
        "FTF-0213",
        "route identity is UNVERIFIED, never fabricated, when no model field is present",
        result.route_identity_state == RouteIdentityState.UNVERIFIED
        and result.resolved_model is None,
    )


def test_non_string_content_is_malformed_response() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _CHAT_STATE.body = json.dumps(
        {
            "id": "chatcmpl-real",
            "model": "gpt-4o-mini-2024-07-18",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None}}],
        }
    ).encode("utf-8")
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0213",
        "a response with no real string content is MALFORMED_RESPONSE, never fabricated",
        result.state == StructuredOutputProbeState.MALFORMED_RESPONSE,
    )


def test_timeout_is_normalized_not_fabricated() -> None:
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
        "FTF-0213",
        "a real connection-level timeout is reported as TIMEOUT, never a hang",
        result.state == StructuredOutputProbeState.TIMEOUT,
    )
    check(
        "FTF-0213",
        "the call actually returned near the bounded timeout, not after the server's full delay",
        elapsed < 1.5,
    )


def test_gateway_permission_denied_is_invalid_token() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_structured_output(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref(),
        secret_config=OpenBaoConfig(address=_SECRET_CONFIG.address, token="wrong-token"),
        policy=_policy_for(port),
    )
    check(
        "FTF-0213",
        "a secret backend that denies access never reaches the gateway as a fabricated success",
        result.state == StructuredOutputProbeState.SECRET_ACCESS_DENIED,
    )

    revoked_result = probe_structured_output(
        base_url=f"https://127.0.0.1:{port}",
        model_id="org-agent-small",
        approved_model_ids=frozenset({"org-agent-small"}),
        secret_ref=_secret_ref(_REVOKED_SECRET_PATH),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
    )
    check(
        "FTF-0213",
        "a real gateway 401 on a resolved-but-stale credential is INVALID_TOKEN",
        revoked_result.state == StructuredOutputProbeState.INVALID_TOKEN,
    )

    real_gateway_result = _probe(port)
    check(
        "FTF-0213",
        "sanity: the real gateway accepts the real secret and reports PROBED",
        real_gateway_result.state == StructuredOutputProbeState.PROBED,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_ChatGatewayHandler)
    port = server.server_address[1]
    result = probe_structured_output(
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
        "FTF-0213",
        "a real, running gateway on an un-allowlisted loopback address is denied pre-connect",
        result.state == StructuredOutputProbeState.DESTINATION_DENIED,
    )


def test_probe_not_authorized_raises_before_any_network_call() -> None:
    raised = False
    try:
        probe_structured_output(
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
            "FTF-0213",
            "the authorization error names the unapproved model, content-free otherwise",
            "unapproved-model" in str(exc),
        )
    check(
        "FTF-0213",
        "an unapproved model raises ChatProbeNotAuthorizedError instead of attempting a probe",
        raised,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    unsupported = probe_structured_output(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://secret/data/x#field",
        secret_config=_SECRET_CONFIG,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0213",
        "an unsupported secret backend scheme is a real, distinct state",
        unsupported.state == StructuredOutputProbeState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = probe_structured_output(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0213",
        "a missing secret backend configuration is a real, distinct state",
        no_config.state == StructuredOutputProbeState.SECRET_BACKEND_ERROR,
    )


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0213",
        "DNS resolution failure classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == StructuredOutputProbeState.DNS_FAILED,
    )
    check(
        "FTF-0213",
        "certificate verification failure classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == StructuredOutputProbeState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0213",
        "a bare TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out")))
        == StructuredOutputProbeState.TIMEOUT,
    )
    check(
        "FTF-0213",
        "a refused connection classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == StructuredOutputProbeState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0213",
        "an unrecognized OSError falls back to unreachable, never invented as success",
        _classify_guard_failure(_wrapped(OSError("something else")))
        == StructuredOutputProbeState.UNREACHABLE,
    )


def test_classify_rejection_is_pure_and_never_guesses() -> None:
    check(
        "FTF-0213",
        "invalid JSON is malformed_response",
        _classify_rejection(b"not json")[0] == StructuredOutputProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0213",
        "JSON with no error object is malformed_response",
        _classify_rejection(b'{"message":"nope"}')[0]
        == StructuredOutputProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0213",
        "a response_format param rejection classifies as unsupported_parameter",
        _classify_rejection(
            json.dumps(
                {"error": {"type": "invalid_request_error", "param": "response_format"}}
            ).encode()
        )[0]
        == StructuredOutputProbeState.UNSUPPORTED_PARAMETER,
    )
    check(
        "FTF-0213",
        "a content_policy_violation code classifies as content_filtered",
        _classify_rejection(json.dumps({"error": {"code": "content_policy_violation"}}).encode())[0]
        == StructuredOutputProbeState.CONTENT_FILTERED,
    )


def test_parse_structured_content_requires_real_string_content() -> None:
    check(
        "FTF-0213",
        "a response with an empty choices list never parses",
        _parse_structured_content(json.dumps({"model": "m", "choices": []}).encode()) is None,
    )
    check(
        "FTF-0213",
        "a response with no choices key at all never parses",
        _parse_structured_content(json.dumps({"model": "m"}).encode()) is None,
    )
    parsed = _parse_structured_content(
        json.dumps({"model": "m", "choices": [{"index": 0, "message": {"content": "{}"}}]}).encode()
    )
    check(
        "FTF-0213",
        "a real minimal completion parses its raw string content and model field",
        parsed is not None and parsed[0] == "{}" and parsed[1] == "m",
    )


def test_validate_against_schema_never_coerces() -> None:
    schema = {
        "type": "object",
        "properties": {
            "word": {"type": "string", "enum": ["pong"]},
            "count": {"type": "integer"},
        },
        "required": ["word", "count"],
        "additionalProperties": False,
    }
    check(
        "FTF-0213",
        "a real well-formed instance validates",
        _validate_against_schema({"word": "pong", "count": 1}, schema),
    )
    check(
        "FTF-0213",
        "a bool is never accepted for an integer field (bool is an int subclass in Python)",
        not _validate_against_schema({"word": "pong", "count": True}, schema),
    )
    check(
        "FTF-0213",
        "a numeric-looking string is never coerced into passing an integer check",
        not _validate_against_schema({"word": "pong", "count": "1"}, schema),
    )
    check(
        "FTF-0213",
        "a missing required key fails validation",
        not _validate_against_schema({"word": "pong"}, schema),
    )
    check(
        "FTF-0213",
        "an undeclared additional property fails validation when additionalProperties is false",
        not _validate_against_schema({"word": "pong", "count": 1, "extra": 1}, schema),
    )
    check(
        "FTF-0213",
        "a value outside the enum fails validation",
        not _validate_against_schema({"word": "ping", "count": 1}, schema),
    )
    check(
        "FTF-0213",
        "a non-object instance against an object schema fails validation",
        not _validate_against_schema(["pong", 1], schema),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0213 real structured-output probe: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0213 real structured-output probe: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
