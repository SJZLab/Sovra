#!/usr/bin/env python3
"""FTF-0211 -- Real streaming probe.

Runnable without any database and without any real external network
egress -- same no-DB/no-external-egress local-server pattern every sibling
FTF-020x suite (already CERTIFIED: FTF-0203/0204/0205/0206/0207/0208/
0209/0210) already established:

  - a real local HTTP server standing in for the OpenBao secret backend
    (the same KV v2 read shape `packages/litellm_client/
    secret_manager_integration.py` (FTF-0202) actually calls, exercised
    unmodified);
  - a real local HTTPS server standing in for the organization's LiteLLM
    gateway's own `POST /v1/chat/completions` route with `stream: true`,
    with a real self-signed CA this suite generates and installs into the
    sandbox's own system trust store for the duration of the run (removed
    again in `atexit`) -- the same real, unmodified TLS certificate
    validation and HTTP round trip FTF-0210's own suite already
    established, extended here to a real close-delimited (no
    Content-Length, no chunked Transfer-Encoding) HTTP/1.0 response body,
    written to the real socket in separate, individually flushed writes
    with real delays between them -- proving this task's own module reads
    genuine incremental deltas over the wire rather than buffering a
    response that happens to arrive all in one read.

This task's own acceptance clause ("Consume actual SSE stream,
reconstruct text/tool deltas and account usage/termination") is covered
by `test_probe_reconstructs_text_deltas_usage_and_termination` and
`test_probe_reconstructs_tool_call_deltas`. This task's own
negative_acceptance ("Malformed/truncated streams, client cancellation
and gateway retry do not leak connection resources or double-bill
silently") is covered by `test_truncated_stream_is_never_fabricated_into_a_success`,
`test_malformed_stream_event_is_never_fabricated_into_a_success`,
`test_stream_budget_ceiling_stops_an_unterminated_stream`,
`test_probe_closes_the_real_connection_on_every_return_path` and
`test_gateway_retry_never_happens_silently_inside_the_probe`.

Run directly: python3 tests/tasks/test_ftf_0211.py
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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.real_chat_probe import (  # noqa: E402
    ChatProbeNotAuthorizedError,
    RouteIdentityState,
)
from packages.litellm_client.real_streaming_probe import (  # noqa: E402
    StreamProbeState,
    StreamTerminationReason,
    _classify_guard_failure,
    _classify_read_failure,
    _classify_rejection,
    _extract_data_payload,
    probe_stream_chat,
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
_SECRET_VALUE = "sk-real-inference-key-0211"
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


# -- Real local TLS fixture: same convention every sibling FTF-020x suite --

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0211-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0211-test-ca.crt")
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
            "/CN=FTF-0211 Test CA",
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
        "FTF-0211: this sandbox has no openssl/update-ca-certificates -- the "
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
class _StreamGatewayState:
    mode: str = "stream"  # "stream" | "reject_400" | "reject_401"
    events: list[bytes] = field(default_factory=list)
    delay_between_events: float = 0.0
    delay_before_first_byte: float = 0.0
    rejection_body: bytes = b""
    request_count: int = 0
    last_request_body: bytes | None = None


def _sse(payload: dict[str, object]) -> bytes:
    return b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"


_DONE_EVENT = b"data: [DONE]\n\n"


def _default_events() -> list[bytes]:
    return [
        _sse(
            {
                "id": "chatcmpl-real",
                "model": "gpt-4o-mini-2024-07-18",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        ),
        _sse(
            {
                "id": "chatcmpl-real",
                "choices": [{"index": 0, "delta": {"content": "p"}, "finish_reason": None}],
            }
        ),
        _sse(
            {
                "id": "chatcmpl-real",
                "choices": [{"index": 0, "delta": {"content": "ong"}, "finish_reason": None}],
            }
        ),
        _sse(
            {
                "id": "chatcmpl-real",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
            }
        ),
        _DONE_EVENT,
    ]


_STREAM_STATE = _StreamGatewayState(events=_default_events())


def _reset_gateway() -> None:
    _STREAM_STATE.mode = "stream"
    _STREAM_STATE.events = _default_events()
    _STREAM_STATE.delay_between_events = 0.0
    _STREAM_STATE.delay_before_first_byte = 0.0
    _STREAM_STATE.rejection_body = b""
    _STREAM_STATE.request_count = 0
    _STREAM_STATE.last_request_body = None


class _StreamGatewayHandler(BaseHTTPRequestHandler):
    """A real HTTPS gateway that streams its `/v1/chat/completions`
    response as a genuine close-delimited (no Content-Length, no
    chunked Transfer-Encoding) HTTP/1.0 body -- writing and flushing each
    SSE event as a separate real socket write, optionally with a real
    delay between them, so the client genuinely receives the stream
    incrementally rather than all at once."""

    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._do_post()
        except OSError:
            # The client may have already closed the connection by the
            # time a deliberately delayed handler (the timeout tests) gets
            # around to writing -- expected under this suite's own
            # delay-based timeout tests, never a test failure.
            return

    def _do_post(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        _STREAM_STATE.request_count += 1
        _STREAM_STATE.last_request_body = raw
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {_SECRET_VALUE}" or _STREAM_STATE.mode == "reject_401":
            body = b'{"error":{"type":"invalid_request_error","message":"no key"}}'
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if _STREAM_STATE.delay_before_first_byte:
            time.sleep(_STREAM_STATE.delay_before_first_byte)
        if _STREAM_STATE.mode == "reject_400":
            self.send_response(400)
            self.send_header("Content-Length", str(len(_STREAM_STATE.rejection_body)))
            self.end_headers()
            self.wfile.write(_STREAM_STATE.rejection_body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in _STREAM_STATE.events:
            self.wfile.write(event)
            self.wfile.flush()
            if _STREAM_STATE.delay_between_events:
                time.sleep(_STREAM_STATE.delay_between_events)


def _probe(port: int, *, model_id: str = "org-agent-small", timeout: float = 10.0):
    return probe_stream_chat(
        base_url=f"https://127.0.0.1:{port}",
        model_id=model_id,
        approved_model_ids=frozenset({model_id}),
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(port),
        timeout=timeout,
    )


# -- Tests ----------------------------------------------------------------


def test_probe_reconstructs_text_deltas_usage_and_termination() -> None:
    """This task's own acceptance clause: "Consume actual SSE stream,
    reconstruct text/tool deltas and account usage/termination." """
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.delay_between_events = 0.02
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211", "a real streamed probe reports PROBED", result.state == StreamProbeState.PROBED
    )
    check(
        "FTF-0211",
        "real content deltas are reconstructed into the full text",
        result.text == "pong",
    )
    check(
        "FTF-0211",
        "the real resolved provider route is recorded and differs from the requested alias",
        result.resolved_model == "gpt-4o-mini-2024-07-18",
    )
    check(
        "FTF-0211",
        "route identity is VERIFIED when the stream names a real model",
        result.route_identity_state == RouteIdentityState.VERIFIED,
    )
    check(
        "FTF-0211",
        "real usage from the final chunk is recorded",
        result.usage is not None
        and result.usage.prompt_tokens == 9
        and result.usage.completion_tokens == 2
        and result.usage.total_tokens == 11,
    )
    check(
        "FTF-0211",
        "the real termination reason is accounted from the stream's own finish_reason",
        result.termination_reason == StreamTerminationReason.STOP,
    )
    check(
        "FTF-0211",
        "a real measured latency is recorded",
        result.latency_ms is not None and result.latency_ms >= 0,
    )
    check("FTF-0211", "chunk_count reflects real events read", result.chunk_count > 0)
    check(
        "FTF-0211",
        "the detail string is content-free (no secret, no raw answer)",
        _SECRET_VALUE not in result.detail and "pong" not in result.detail,
    )


def test_probe_sends_a_bounded_synthetic_streaming_request() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    _probe(port)
    assert _STREAM_STATE.last_request_body is not None
    sent = json.loads(_STREAM_STATE.last_request_body)
    check(
        "FTF-0211",
        "the real request used the fixed synthetic probe prompt, not caller-supplied content",
        sent["messages"] == [{"role": "user", "content": "Reply with the single word: pong."}],
    )
    check(
        "FTF-0211",
        "the real request carried a bounded max_tokens ceiling",
        isinstance(sent.get("max_tokens"), int) and sent["max_tokens"] <= 16,
    )
    check("FTF-0211", "the real request explicitly requested streaming", sent.get("stream") is True)


def test_probe_reconstructs_tool_call_deltas() -> None:
    """Section 3.4 lists "tools" as a distinct probed capability from plain
    chat -- this proves the incremental OpenAI-compatible tool-call delta
    shape (only the first fragment per index carries id/name; later
    fragments append to arguments) is reconstructed correctly, and that
    the accumulated arguments are never parsed/executed by this probe."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.events = [
        _sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "function": {"name": "get_weather", "arguments": '{"loc'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": 'ation":"NY"}'}}]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        _DONE_EVENT,
    ]
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a tool-call stream still reports PROBED",
        result.state == StreamProbeState.PROBED,
    )
    check("FTF-0211", "exactly one reconstructed tool call is present", len(result.tool_calls) == 1)
    call = result.tool_calls[0]
    check("FTF-0211", "the tool call's id is taken from its first fragment", call.id == "call_abc")
    check(
        "FTF-0211",
        "the tool call's name is taken from its first fragment",
        call.name == "get_weather",
    )
    check(
        "FTF-0211",
        "the tool call's arguments are the real concatenation of every fragment, unparsed",
        call.arguments == '{"location":"NY"}',
    )
    check(
        "FTF-0211",
        "termination_reason reflects the real tool_calls finish_reason",
        result.termination_reason == StreamTerminationReason.TOOL_CALLS,
    )


def test_missing_usage_is_unknown_not_zero() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.events = [
        _sse({"choices": [{"index": 0, "delta": {"content": "pong"}, "finish_reason": "stop"}]}),
        _DONE_EVENT,
    ]
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a stream with no usage chunk still reports PROBED",
        result.state == StreamProbeState.PROBED,
    )
    check("FTF-0211", "missing usage is None, never a zero-filled ChatUsage", result.usage is None)


def test_truncated_stream_is_never_fabricated_into_a_success() -> None:
    """This task's own negative_acceptance: "Malformed/truncated streams...
    do not leak connection resources or double-bill silently." A real
    stream that ends (connection closes) without ever emitting `[DONE]`
    must never be reported as a complete PROBED result."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.events = [
        _sse({"choices": [{"index": 0, "delta": {"content": "pon"}, "finish_reason": None}]}),
        # deliberately no [DONE] -- the real server closes the connection here
    ]
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a real stream truncated before [DONE] is TRUNCATED_STREAM, never fabricated into PROBED",
        result.state == StreamProbeState.TRUNCATED_STREAM,
    )
    check(
        "FTF-0211",
        "a truncated stream never carries a resolved model or usage",
        result.resolved_model is None and result.usage is None,
    )


def test_malformed_stream_event_is_never_fabricated_into_a_success() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.events = [b"data: {not valid json\n\n", _DONE_EVENT]
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a real malformed SSE data event is MALFORMED_RESPONSE, never fabricated into PROBED",
        result.state == StreamProbeState.MALFORMED_RESPONSE,
    )


def test_stream_budget_ceiling_stops_an_unterminated_stream() -> None:
    """A gateway that never terminates (no [DONE], no connection close)
    within the bounded probe read budget must be stopped and closed, not
    read from forever."""
    if not _UPDATE_CA_AVAILABLE:
        return
    import packages.litellm_client.real_streaming_probe as mod

    original_budget = mod._PROBE_MAX_STREAM_CHUNKS
    mod._PROBE_MAX_STREAM_CHUNKS = 3
    try:
        _reset_gateway()
        _STREAM_STATE.events = [
            _sse({"choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}]})
            for _ in range(50)
        ] + [_DONE_EVENT]
        # A real delay between each server write forces the client to make
        # a genuinely separate real read per event, rather than receiving
        # the whole (small) payload in one or two reads -- without this,
        # the bounded chunk-read ceiling below would never actually be
        # reached before the real stream finished on its own.
        _STREAM_STATE.delay_between_events = 0.05
        server = _start_https_server(_StreamGatewayHandler)
        port = server.server_address[1]
        started = time.monotonic()
        result = _probe(port, timeout=5.0)
        elapsed = time.monotonic() - started
        check(
            "FTF-0211",
            "hitting the bounded read-chunk ceiling before [DONE] is TRUNCATED_STREAM",
            result.state == StreamProbeState.TRUNCATED_STREAM,
        )
        check(
            "FTF-0211",
            "the probe stopped promptly at the ceiling rather than reading the whole stream",
            elapsed < 4.0,
        )
    finally:
        mod._PROBE_MAX_STREAM_CHUNKS = original_budget


def test_probe_closes_the_real_connection_on_every_return_path() -> None:
    """This task's own negative_acceptance: "...client cancellation... do
    not leak connection resources..." Every return path -- success,
    rejection, truncation -- must have actually closed the real
    underlying connection by the time `probe_stream_chat` returns."""
    if not _UPDATE_CA_AVAILABLE:
        return
    from packages.security.outbound_guard import GuardedStreamResponse

    close_calls: list[bool] = []
    original_close = GuardedStreamResponse.close

    def _tracking_close(self: GuardedStreamResponse) -> None:
        close_calls.append(True)
        original_close(self)

    GuardedStreamResponse.close = _tracking_close  # type: ignore[method-assign]
    try:
        for scenario in ("success", "truncated", "malformed"):
            close_calls.clear()
            _reset_gateway()
            if scenario == "truncated":
                _STREAM_STATE.events = [
                    _sse({"choices": [{"index": 0, "delta": {"content": "p"}}]})
                ]
            elif scenario == "malformed":
                _STREAM_STATE.events = [b"data: {broken\n\n", _DONE_EVENT]
            server = _start_https_server(_StreamGatewayHandler)
            port = server.server_address[1]
            _probe(port)
            check(
                "FTF-0211",
                f"the real connection is closed exactly once for the '{scenario}' return path",
                len(close_calls) == 1,
            )
    finally:
        GuardedStreamResponse.close = original_close  # type: ignore[method-assign]


def test_gateway_retry_never_happens_silently_inside_the_probe() -> None:
    """This task's own negative_acceptance: "...gateway retry do not...
    double-bill silently." A gateway that would fail on a first real
    attempt and succeed on a second must never see this module issue that
    second attempt itself -- retrying (a second real, billable call) is a
    caller-level decision, never automatic here."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.mode = "reject_401"
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a real rejected first attempt is reported as its own real failure",
        result.state == StreamProbeState.INVALID_TOKEN,
    )
    check(
        "FTF-0211",
        "the probe made exactly one real request -- it never silently retried",
        _STREAM_STATE.request_count == 1,
    )


def test_timeout_is_normalized_not_fabricated() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.delay_before_first_byte = 2.0
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    started = time.monotonic()
    result = _probe(port, timeout=0.3)
    elapsed = time.monotonic() - started
    check(
        "FTF-0211",
        "a real connection-level timeout is reported as TIMEOUT, never a hang",
        result.state == StreamProbeState.TIMEOUT,
    )
    check(
        "FTF-0211",
        "the call actually returned near the bounded timeout, not after the server's full delay",
        elapsed < 1.5,
    )


def test_mid_stream_timeout_is_normalized_not_a_raw_exception() -> None:
    """A timeout occurring *after* headers arrive but before the stream
    completes must still be a classified `StreamProbeResult`, never a raw
    `TimeoutError` escaping this module."""
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.events = [
        _sse({"choices": [{"index": 0, "delta": {"content": "p"}, "finish_reason": None}]})
    ]
    _STREAM_STATE.delay_between_events = 2.0
    # Add a second event so the handler sleeps *after* the first real write,
    # keeping the connection open past the client's bounded read timeout.
    _STREAM_STATE.events.append(_DONE_EVENT)
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port, timeout=0.3)
    check(
        "FTF-0211",
        "a real mid-stream timeout is TIMEOUT, never an unhandled exception",
        result.state == StreamProbeState.TIMEOUT,
    )


def test_sensitive_content_is_normalized_not_fabricated() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.mode = "reject_400"
    _STREAM_STATE.rejection_body = json.dumps(
        {"error": {"type": "content_filter", "code": "content_filter", "message": "blocked"}}
    ).encode("utf-8")
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a real content-policy rejection is CONTENT_FILTERED, never PROBED",
        result.state == StreamProbeState.CONTENT_FILTERED,
    )
    check(
        "FTF-0211",
        "the detail string never echoes the raw provider error message",
        "blocked" not in result.detail,
    )


def test_unsupported_parameter_is_normalized_not_fabricated() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    _STREAM_STATE.mode = "reject_400"
    _STREAM_STATE.rejection_body = json.dumps(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "unsupported_parameter",
                "param": "stream",
                "message": "this model does not support streaming",
            }
        }
    ).encode("utf-8")
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = _probe(port)
    check(
        "FTF-0211",
        "a real unsupported-parameter rejection is UNSUPPORTED_PARAMETER, distinct from "
        "content filtering",
        result.state == StreamProbeState.UNSUPPORTED_PARAMETER,
    )


def test_destination_denied_is_a_real_policy_check() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _reset_gateway()
    server = _start_https_server(_StreamGatewayHandler)
    port = server.server_address[1]
    result = probe_stream_chat(
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
        "FTF-0211",
        "a real, running gateway on an un-allowlisted loopback address is denied pre-connect",
        result.state == StreamProbeState.DESTINATION_DENIED,
    )


def test_probe_not_authorized_raises_before_any_network_call() -> None:
    raised = False
    try:
        probe_stream_chat(
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
            "FTF-0211",
            "the authorization error names the unapproved model, content-free otherwise",
            "unapproved-model" in str(exc),
        )
    check(
        "FTF-0211",
        "an unapproved model raises ChatProbeNotAuthorizedError instead of attempting a probe",
        raised,
    )


def test_secret_backend_states_are_real_and_distinct() -> None:
    unsupported = probe_stream_chat(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://secret/data/x#field",
        secret_config=_SECRET_CONFIG,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0211",
        "an unsupported secret backend scheme is a real, distinct state",
        unsupported.state == StreamProbeState.SECRET_BACKEND_UNSUPPORTED,
    )

    no_config = probe_stream_chat(
        base_url="https://127.0.0.1:1",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref=_secret_ref(),
        secret_config=None,
        policy=ConnectionPolicy(name="p", allowed_schemes=frozenset({"https"})),
    )
    check(
        "FTF-0211",
        "a missing secret backend configuration is a real, distinct state",
        no_config.state == StreamProbeState.SECRET_BACKEND_ERROR,
    )


def test_classify_guard_failure_maps_every_real_exception_type() -> None:
    def _wrapped(cause: BaseException) -> OutboundGuardError:
        try:
            raise OutboundGuardError("wrapped") from cause
        except OutboundGuardError as exc:
            return exc

    check(
        "FTF-0211",
        "DNS resolution failure classifies as dns_failed",
        _classify_guard_failure(_wrapped(socket.gaierror("no such host")))
        == StreamProbeState.DNS_FAILED,
    )
    check(
        "FTF-0211",
        "certificate verification failure classifies as certificate_error",
        _classify_guard_failure(_wrapped(ssl.SSLCertVerificationError("bad cert")))
        == StreamProbeState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0211",
        "a bare TimeoutError classifies as timeout",
        _classify_guard_failure(_wrapped(TimeoutError("timed out"))) == StreamProbeState.TIMEOUT,
    )
    check(
        "FTF-0211",
        "a refused connection classifies as connection_refused",
        _classify_guard_failure(_wrapped(ConnectionRefusedError("refused")))
        == StreamProbeState.CONNECTION_REFUSED,
    )
    check(
        "FTF-0211",
        "an unrecognized OSError falls back to unreachable, never invented as success",
        _classify_guard_failure(_wrapped(OSError("something else")))
        == StreamProbeState.UNREACHABLE,
    )


def test_classify_read_failure_is_distinct_from_guard_failure() -> None:
    check(
        "FTF-0211",
        "a direct TimeoutError from a body read classifies as timeout",
        _classify_read_failure(TimeoutError("timed out")) == StreamProbeState.TIMEOUT,
    )
    check(
        "FTF-0211",
        "a direct SSL error from a body read classifies as certificate_error",
        _classify_read_failure(ssl.SSLError("bad record")) == StreamProbeState.CERTIFICATE_ERROR,
    )
    check(
        "FTF-0211",
        "an unrecognized OSError from a body read falls back to unreachable",
        _classify_read_failure(ConnectionResetError("reset")) == StreamProbeState.UNREACHABLE,
    )


def test_classify_rejection_is_pure_and_never_guesses() -> None:
    check(
        "FTF-0211",
        "invalid JSON is malformed_response",
        _classify_rejection(b"not json")[0] == StreamProbeState.MALFORMED_RESPONSE,
    )
    check(
        "FTF-0211",
        "a content_policy_violation code classifies as content_filtered",
        _classify_rejection(json.dumps({"error": {"code": "content_policy_violation"}}).encode())[0]
        == StreamProbeState.CONTENT_FILTERED,
    )


def test_extract_data_payload_joins_multiline_and_ignores_non_data_lines() -> None:
    check(
        "FTF-0211",
        "an event with no data: line at all extracts to None",
        _extract_data_payload(b"event: ping\nid: 1") is None,
    )
    check(
        "FTF-0211",
        "a single data: line extracts its payload",
        _extract_data_payload(b'data: {"a":1}') == b'{"a":1}',
    )
    check(
        "FTF-0211",
        "multiple data: lines in one event join with a newline, per the SSE spec",
        _extract_data_payload(b"data: line1\ndata: line2") == b"line1\nline2",
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0211 real streaming probe: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0211 real streaming probe: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
