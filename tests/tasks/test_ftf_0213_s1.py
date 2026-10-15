#!/usr/bin/env python3
"""FTF-0213-S1 -- Real constrained-choice enforcement and output-token probe.

Same no-DB/no-external-egress pattern as tests/tasks/test_ftf_0213.py: a real
local HTTP server standing in for OpenBao (the FTF-0202 KV v2 read, unmodified)
and a real local HTTPS server standing in for the LiteLLM gateway with a real
self-signed CA installed for the run, so every state is reached over a real
TLS-validated HTTP round trip. The stand-in's behaviour is swapped per check:

  enforcing, prompt-following, param-dropping, rejecting (400), truncating,
  no-usage, extra-key, numeric-string coercion, wrong-enum (a member of a
  *different* trial's enum), duplicate key, over-budget usage, content filter.

Each behaviour maps to exactly one state. Request body, trial count, enum
distinctness, token ceiling, and "unapproved model opens no socket" are
asserted over the real wire.

Run directly: python3 tests/tasks/test_ftf_0213_s1.py
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client import real_constrained_decoding_probe as probe_module  # noqa: E402
from packages.litellm_client.real_chat_probe import (  # noqa: E402
    ChatProbeNotAuthorizedError,
    RouteIdentityState,
)
from packages.litellm_client.real_constrained_decoding_probe import (  # noqa: E402
    ConstrainedDecodingProbeState as S,
)
from packages.litellm_client.real_constrained_decoding_probe import (  # noqa: E402
    probe_constrained_choice,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.security.outbound_guard import OutboundGuardError  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0213-S1"


def check(description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{CID}] {description}")


# -- Real local secret-backend fixture (OpenBao KV v2 read subset) ----------

_SECRET_VALUE = "sk-real-inference-key-0213s1"
_SECRET_TOKEN = "test-vault-token"
_SECRET_PATH = "/v1/secret/data/litellm/org1"
_SECRET_HITS = [0]


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        _SECRET_HITS[0] += 1
        ok = self.headers.get("X-Vault-Token") == _SECRET_TOKEN and self.path == _SECRET_PATH
        status = 200 if ok else 403
        payload: dict[str, object] = {
            "data": {"data": {"api_key": _SECRET_VALUE}, "metadata": {"version": 1}}
        }
        body = json.dumps(payload if ok else {"errors": ["denied"]}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)
_SECRET_CONFIG = OpenBaoConfig(
    address=f"http://127.0.0.1:{_SECRET_SERVER.server_address[1]}", token=_SECRET_TOKEN
)
_SECRET_REF = "openbao://secret/litellm/org1/api_key"

# -- Real local TLS fixture -------------------------------------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0213s1-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0213s1-test-ca.crt")
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key, ca_crt = _TLS_TMP / "ca.key", _TLS_TMP / "ca.crt"
    key, csr, crt, ext = (_TLS_TMP / n for n in ("leaf.key", "leaf.csr", "leaf.crt", "leaf.ext"))
    _openssl("genrsa", "-out", str(ca_key), "2048")
    _openssl(
        *("req", "-x509", "-new", "-key", str(ca_key), "-sha256", "-days", "2"),
        *("-out", str(ca_crt), "-subj", "/CN=FTF-0213-S1 Test CA"),
        # Strict verifiers (Python 3.13+/OpenSSL 3) reject a CA without these.
        *("-addext", "basicConstraints=critical,CA:TRUE"),
        *("-addext", "keyUsage=critical,keyCertSign,cRLSign"),
    )
    _openssl("genrsa", "-out", str(key), "2048")
    _openssl("req", "-new", "-key", str(key), "-out", str(csr), "-subj", "/CN=127.0.0.1")
    ext.write_text("subjectAltName=IP:127.0.0.1\n")
    _openssl(
        *("x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key)),
        *("-CAcreateserial", "-out", str(crt), "-days", "2", "-sha256", "-extfile", str(ext)),
    )
    subprocess.run(["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)], check=True)
    subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    atexit.register(_remove_ca_trust)
    return crt, key


_LEAF: Path | None = None
_LEAF_KEY: Path | None = None
if _UPDATE_CA_AVAILABLE:
    _LEAF, _LEAF_KEY = _generate_trusted_server_cert()
else:
    print(
        "FTF-0213-S1: no openssl/update-ca-certificates/sudo in this sandbox -- the live "
        "TLS-backed checks are skipped and disclosed; pure checks still run. CI has all three."
    )


def _policy_for(port: int) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


# -- Stand-in gateway --------------------------------------------------------


@dataclass
class _Gateway:
    mode: str = "enforcing"
    bodies: list[dict[str, object]] = field(default_factory=list)
    resolved_model: str | None = "gpt-4o-mini-2024-07-18"

    def reset(self, mode: str = "enforcing") -> None:
        self.mode = mode
        self.bodies = []
        self.resolved_model = "gpt-4o-mini-2024-07-18"


_GW = _Gateway()


def _enum_of(body: dict[str, object]) -> list[str]:
    rf = body["response_format"]
    assert isinstance(rf, dict)
    return list(rf["json_schema"]["schema"]["properties"]["label"]["enum"])


def _completion(content: str, *, finish: str = "stop", usage: object = "default") -> bytes:
    payload: dict[str, object] = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
    }
    if _GW.resolved_model is not None:
        payload["model"] = _GW.resolved_model
    if usage == "default":
        payload["usage"] = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
    elif usage is not None:
        payload["usage"] = usage
    return json.dumps(payload).encode()


# Per-trial completion_tokens a gateway can report over the wire, by mode.
_TOKEN_COUNT_MODES: dict[str, tuple[int, int, int]] = {
    "tokens_negative_all": (-5, -5, -5),
    "tokens_zero_all": (0, 0, 0),
    "tokens_zero_one": (5, 0, 5),
    "tokens_negative_one": (5, -1, 5),
    "tokens_zero_and_negative": (0, -1, 5),
}


def _respond_for(body: dict[str, object]) -> tuple[int, bytes]:
    enum = _enum_of(body)
    mode = _GW.mode
    member = json.dumps({"label": enum[0]})
    if mode == "enforcing":
        return 200, _completion(member)
    if mode == "prompt_following":
        return 200, _completion(json.dumps({"label": "banana"}))
    if mode == "param_dropping":
        return 200, _completion("banana")
    if mode == "rejecting":
        err = {"error": {"type": "invalid_request_error", "param": "response_format"}}
        return 400, json.dumps(err).encode()
    if mode == "mid_run_reject":
        # V16: trial 1 (the first POST _respond_for ever sees for this run) succeeds; every
        # later trial is rejected -- a later-trial 400 must stop the loop immediately, never
        # let it continue to a further trial.
        if len(_GW.bodies) == 1:
            return 200, _completion(member)
        err = {"error": {"type": "invalid_request_error", "param": "response_format"}}
        return 400, json.dumps(err).encode()
    if mode in _TOKEN_COUNT_MODES:
        # USAGE-IMPOSSIBLE-COUNT-WIRE: the gateway reports an impossible completion_tokens on the
        # trial(s) the mode names; the answer itself is a perfectly valid in-enum "stop".
        trial = len(_GW.bodies)  # 1-based: _GatewayHandler appends before responding
        counts = _TOKEN_COUNT_MODES[mode]
        usage = {"prompt_tokens": 20, "completion_tokens": counts[trial - 1], "total_tokens": 25}
        return 200, _completion(member, usage=usage)
    if mode == "truncating":
        return 200, _completion(member, finish="length")
    if mode == "no_usage":
        return 200, _completion(member, usage=None)
    if mode == "extra_key":
        return 200, _completion(json.dumps({"label": enum[0], "why": "x"}))
    if mode == "coerced":
        return 200, _completion(json.dumps({"label": 1}))
    if mode == "wrong_enum":
        other = next(t for t in probe_module._VOCABULARY if t not in enum)
        return 200, _completion(json.dumps({"label": other}))
    if mode == "duplicate_key":
        return 200, _completion('{"label": "banana", "label": "' + enum[0] + '"}')
    if mode == "over_budget":
        usage = {"prompt_tokens": 20, "completion_tokens": 99, "total_tokens": 119}
        return 200, _completion(member, usage=usage)
    if mode == "content_filter":
        return 200, _completion(member, finish="content_filter")
    if mode == "tool_calls_finish":
        return 200, _completion(member, finish="tool_calls")
    if mode == "malformed":
        return 200, b'{"choices": []}'
    if mode == "unauthorized":
        return 401, b"{}"
    if mode == "server_error":
        return 500, b""
    raise AssertionError(mode)


class _GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        _GW.bodies.append(body)
        if self.path != "/v1/chat/completions":
            status, payload = 404, b""
        else:
            status, payload = _respond_for(body)
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_gateway() -> int:
    assert _LEAF is not None and _LEAF_KEY is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(_LEAF), keyfile=str(_LEAF_KEY))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return int(server.server_address[1])


_PORT: list[int] = []


def _run(mode: str, *, rng: object = None, model_id: str = "org-agent-small"):
    _GW.reset(mode)
    if not _PORT:
        _PORT.append(_start_gateway())
    kwargs = {"rng": rng} if rng is not None else {}
    return probe_constrained_choice(
        base_url=f"https://127.0.0.1:{_PORT[0]}",
        model_id=model_id,
        approved_model_ids=frozenset({model_id}),
        secret_ref=_SECRET_REF,
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(_PORT[0]),
        **kwargs,  # type: ignore[arg-type]
    )


# -- Tests -------------------------------------------------------------------

_STATE_FOR_MODE = {
    "enforcing": S.CONSTRAINED_OBSERVED,
    "prompt_following": S.NOT_ENFORCED,
    "param_dropping": S.NOT_ENFORCED,
    "rejecting": S.UNSUPPORTED_PARAMETER,
    "truncating": S.TRUNCATED,
    "no_usage": S.USAGE_MISSING,
    "extra_key": S.NOT_ENFORCED,
    "coerced": S.NOT_ENFORCED,
    "wrong_enum": S.NOT_ENFORCED,
    "duplicate_key": S.NOT_ENFORCED,
    "over_budget": S.MALFORMED_RESPONSE,
    "tokens_negative_all": S.MALFORMED_RESPONSE,
    "tokens_zero_all": S.USAGE_MISSING,
    "tokens_zero_one": S.USAGE_MISSING,
    "tokens_negative_one": S.MALFORMED_RESPONSE,
    "tokens_zero_and_negative": S.MALFORMED_RESPONSE,
    "content_filter": S.CONTENT_FILTERED,
    "tool_calls_finish": S.NOT_ENFORCED,
    "malformed": S.MALFORMED_RESPONSE,
    "unauthorized": S.INVALID_TOKEN,
    "server_error": S.UNREACHABLE,
}


def test_every_behaviour_maps_to_exactly_its_state() -> None:
    """Negative acceptance on the real default code: the attack input is
    present (a route that follows the prompt, drops the parameter, coerces,
    adds keys, answers from another trial's enum) and the bad outcome
    (CONSTRAINED_OBSERVED) must not happen."""
    if not _UPDATE_CA_AVAILABLE:
        return
    for mode, expected in _STATE_FOR_MODE.items():
        result = _run(mode)
        check(
            f"mode {mode!r} -> {expected.value}, got {result.state.value}", result.state == expected
        )
        if expected is not S.CONSTRAINED_OBSERVED:
            check(
                f"mode {mode!r} must never report CONSTRAINED_OBSERVED",
                result.state != S.CONSTRAINED_OBSERVED,
            )


def test_enforcing_route_records_trials_identity_and_tokens() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    result = _run("enforcing")
    check("three trials were run", len(result.trials) == 3 and len(_GW.bodies) == 3)
    check("every trial's label was in its own enum", all(t.label_in_enum for t in result.trials))
    check("every trial finished with stop", all(t.finish_reason == "stop" for t in result.trials))
    check("route identity verified", result.route_identity_state == RouteIdentityState.VERIFIED)
    check("resolved model recorded", result.resolved_model == "gpt-4o-mini-2024-07-18")
    check(
        "every trial records a verified identity",
        all(t.route_identity_state == RouteIdentityState.VERIFIED for t in result.trials),
    )
    check("max completion tokens observed", result.max_completion_tokens_observed == 5)
    check("mean completion tokens is a Decimal", result.mean_completion_tokens == Decimal(5))
    check("latency recorded per trial", all(t.latency_ms >= 0 for t in result.trials))
    check(
        "detail is content-free",
        "banana" not in result.detail and _SECRET_VALUE not in result.detail,
    )


def test_mid_run_400_stops_after_exactly_two_requests() -> None:
    """V16: a 400 on a later trial (not the first) must stop the probe immediately -- never
    let the loop continue to the next trial on the strength of an earlier success."""
    if not _UPDATE_CA_AVAILABLE:
        return
    result = _run("mid_run_reject")
    check("mid-run 400 is unsupported_parameter", result.state == S.UNSUPPORTED_PARAMETER)
    check("exactly two requests were made", len(_GW.bodies) == 2)
    check("the third trial was never attempted", len(_GW.bodies) != 3)
    check("only the first (successful) trial's result was kept", len(result.trials) == 1)


def test_impossible_token_counts_over_the_real_wire_are_never_a_metric() -> None:
    """USAGE-IMPOSSIBLE-COUNT-WIRE: negative and zero completion_tokens arrive through the
    real parse path (gateway -> TLS -> _parse_trial_body -> trial loop -> _aggregate_trials),
    so a clamp (max(..., 1)), abs(), or a per-trial drop of the bad value in the trial loop
    cannot hide behind the direct _aggregate_trials pins."""
    if not _UPDATE_CA_AVAILABLE:
        return
    for mode, counts in _TOKEN_COUNT_MODES.items():
        result = _run(mode)
        expected = S.MALFORMED_RESPONSE if any(c < 0 for c in counts) else S.USAGE_MISSING
        check(f"{mode}: state {expected.value}, got {result.state.value}", result.state == expected)
        check(f"{mode}: all three trials were requested", len(_GW.bodies) == 3)
        check(f"{mode}: never CONSTRAINED_OBSERVED", result.state != S.CONSTRAINED_OBSERVED)
        check(f"{mode}: no max claim", result.max_completion_tokens_observed is None)
        check(f"{mode}: no mean claim", result.mean_completion_tokens is None)
        check(
            f"{mode}: the raw per-trial counts are kept verbatim, got "
            f"{[t.completion_tokens for t in result.trials]}",
            tuple(t.completion_tokens for t in result.trials) == counts,
        )
        check(
            f"{mode}: the answers themselves were valid in-enum stops",
            all(t.label_in_enum and t.finish_reason == "stop" for t in result.trials),
        )


def test_usage_missing_is_unknown_not_zero() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    result = _run("no_usage")
    check("USAGE_MISSING reports no max token claim", result.max_completion_tokens_observed is None)
    check("USAGE_MISSING reports no mean token claim", result.mean_completion_tokens is None)
    check(
        "per-trial completion tokens are None, not 0",
        all(t.completion_tokens is None for t in result.trials),
    )


def test_token_mean_and_max_use_real_usage() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    counts = iter([2, 4, 9])
    original = _respond_for

    def varying(body: dict[str, object]) -> tuple[int, bytes]:
        usage = {"prompt_tokens": 1, "completion_tokens": next(counts), "total_tokens": 1}
        return 200, _completion(json.dumps({"label": _enum_of(body)[0]}), usage=usage)

    globals()["_respond_for"] = varying
    try:
        result = _run("enforcing")
    finally:
        globals()["_respond_for"] = original
    check("max is the largest real count", result.max_completion_tokens_observed == 9)
    check("mean is exact", result.mean_completion_tokens == Decimal(15) / Decimal(3))


def test_request_body_is_portable_bounded_and_distinct_per_trial() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    _run("enforcing")
    enums = [_enum_of(b) for b in _GW.bodies]
    check("exactly K=3 requests", len(_GW.bodies) == 3)
    check("each enum has three nonsense tokens", all(len(e) == 3 for e in enums))
    flat = [t for e in enums for t in e]
    check("enums are disjoint across trials", len(set(flat)) == 9)
    check("enum tokens come from the fixed vocabulary", set(flat) <= set(probe_module._VOCABULARY))
    for body in _GW.bodies:
        check("max_tokens is 16", body.get("max_tokens") == 16)
        check("temperature is 0", body.get("temperature") == 0)
        check("stream is false", body.get("stream") is False)
        rf = body.get("response_format")
        check(
            "standard json_schema response_format",
            isinstance(rf, dict) and rf.get("type") == "json_schema",
        )
        js = rf["json_schema"] if isinstance(rf, dict) else {}
        check("strict mode requested", js.get("strict") is True)
        schema = js.get("schema", {})
        check("schema forbids extra keys", schema.get("additionalProperties") is False)
        check("label is the one required property", schema.get("required") == ["label"])
        check(
            "prompt is adversarial: asks for a word outside the enum",
            body["messages"] == [{"role": "user", "content": "Reply with the single word: banana"}]
            and "banana" not in _enum_of(body),
        )
        vendor = {
            "guided_choice",
            "guided_json",
            "guided_regex",
            "structured_outputs",
            "extra_body",
        }
        check("no vendor-only extras are sent", not (vendor & set(body)))


def test_unapproved_model_opens_no_socket_and_reads_no_secret() -> None:
    _GW.reset()
    hits = _SECRET_HITS[0]
    raised = False
    try:
        probe_constrained_choice(
            base_url="https://127.0.0.1:9",
            model_id="org-agent-small",
            approved_model_ids=frozenset({"other"}),
            secret_ref=_SECRET_REF,
            secret_config=_SECRET_CONFIG,
            policy=_policy_for(9),
        )
    except ChatProbeNotAuthorizedError:
        raised = True
    check("unapproved model raises ChatProbeNotAuthorizedError", raised)
    check("no gateway request was made", _GW.bodies == [])
    check("no secret was read before authorization", _SECRET_HITS[0] == hits)


def test_prompt_budget_and_trials_are_not_caller_supplied() -> None:
    unchecked: Any = probe_constrained_choice
    for name in ("prompt", "max_tokens", "trials", "temperature", "response_format"):
        raised = False
        try:
            unchecked(
                base_url="https://127.0.0.1:9",
                model_id="m",
                approved_model_ids=frozenset({"m"}),
                secret_ref=_SECRET_REF,
                secret_config=_SECRET_CONFIG,
                policy=_policy_for(9),
                **{name: "x"},
            )
        except TypeError:
            raised = True
        check(f"caller-supplied {name!r} is refused", raised)


def test_rng_is_injectable_and_must_return_distinct_tokens() -> None:
    class Fixed:
        def sample(self, population, k):  # noqa: ANN001, ANN201
            return list(population)[:k]

    class Repeating:
        def sample(self, population, k):  # noqa: ANN001, ANN201
            return [list(population)[0]] * k

    if _UPDATE_CA_AVAILABLE:
        _run("enforcing", rng=Fixed())
        first = [_enum_of(b) for b in _GW.bodies]
        _run("enforcing", rng=Fixed())
        check(
            "an injected rng makes the draw deterministic",
            first == [_enum_of(b) for b in _GW.bodies],
        )
    raised = False
    try:
        probe_constrained_choice(
            base_url="https://127.0.0.1:9",
            model_id="m",
            approved_model_ids=frozenset({"m"}),
            secret_ref=_SECRET_REF,
            secret_config=_SECRET_CONFIG,
            policy=_policy_for(9),
            rng=Repeating(),
        )
    except ValueError:
        raised = True
    check("an rng that repeats tokens is refused before any request", raised)
    check("vocabulary excludes the prompted word", "banana" not in probe_module._VOCABULARY)


def test_secret_and_transport_failures_are_distinct_states() -> None:
    result = probe_constrained_choice(
        base_url="https://127.0.0.1:9",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://nope",
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(9),
    )
    check(
        "unsupported secret scheme is its own state", result.state == S.SECRET_BACKEND_UNSUPPORTED
    )
    result = probe_constrained_choice(
        base_url="https://127.0.0.1:9",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref=_SECRET_REF,
        secret_config=None,
        policy=_policy_for(9),
    )
    check("missing secret config is its own state", result.state == S.SECRET_BACKEND_ERROR)
    result = probe_constrained_choice(
        base_url="https://127.0.0.1:9",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="openbao://secret/missing/path/api_key",
        secret_config=_SECRET_CONFIG,
        policy=_policy_for(9),
    )
    check(
        "denied/missing secret is not a probe success",
        result.state in (S.SECRET_NOT_FOUND, S.SECRET_ACCESS_DENIED),
    )


def test_label_membership_is_exact() -> None:
    member = probe_module._label_is_member
    enum = ("a", "b", "c")
    check("exact member passes", member('{"label": "a"}', enum))
    check("prose fails", not member("a", enum))
    check("numeric label fails", not member('{"label": 1}', enum))
    check("string-number coercion fails", not member('{"label": "1"}', enum))
    check("extra key fails", not member('{"label": "a", "x": 1}', enum))
    check("missing key fails", not member("{}", enum))
    check("array fails", not member('["a"]', enum))
    check("duplicate key fails", not member('{"label": "z", "label": "a"}', enum))
    check("non-member fails", not member('{"label": "z"}', enum))
    check("unhashable label fails without TypeError", not member('{"label": ["a"]}', enum))
    check("null label fails", not member('{"label": null}', enum))
    check("a case/space-folded key is not the label key", not member('{" Label ": "a"}', enum))
    check("a case-folded label is not a member", not member('{"label": "A"}', enum))
    keys = probe_module._no_duplicate_keys
    check(
        "distinct keys that differ only by case are both kept",
        keys([("a", 1), ("A", 2)]) == {"a": 1, "A": 2},
    )
    check(
        "distinct keys that differ only by padding are both kept",
        keys([("a", 1), (" a", 2)]) == {"a": 1, " a": 2},
    )
    raised = False
    try:
        keys([("a", 1), ("a", 2)])
    except ValueError:
        raised = True
    check("an exactly repeated key is refused", raised)
    check("a space-padded label is not a member", not member('{"label": " a "}', enum))


def _trial(finish: str = "stop", tokens: int | None = 5, member: bool = True, ident=None):
    return probe_module.ConstrainedTrial(
        finish_reason=finish,
        completion_tokens=tokens,
        label_in_enum=member,
        route_identity_state=ident or RouteIdentityState.VERIFIED,
        latency_ms=1.0,
    )


def _aggregate(trials, models=None):
    models = models if models is not None else ["m"] * len(trials)
    return probe_module._aggregate_trials(tuple(trials), models, datetime.now(UTC))


def test_aggregate_states_are_exact_and_case_sensitive() -> None:
    three = lambda **kw: [_trial(**kw) for _ in range(3)]  # noqa: E731
    check(
        "all-good aggregates to CONSTRAINED_OBSERVED",
        _aggregate(three()).state == S.CONSTRAINED_OBSERVED,
    )
    check(
        "exactly 16 tokens is within the ceiling",
        _aggregate(three(tokens=16)).state == S.CONSTRAINED_OBSERVED,
    )
    check(
        "17 tokens exceeds the ceiling", _aggregate(three(tokens=17)).state == S.MALFORMED_RESPONSE
    )
    check("length is TRUNCATED", _aggregate(three(finish="length")).state == S.TRUNCATED)
    check(
        "one truncated trial is TRUNCATED",
        _aggregate([_trial(), _trial(), _trial("length")]).state == S.TRUNCATED,
    )
    check(
        "'Length' is not the exact value, so not TRUNCATED",
        _aggregate(three(finish="Length")).state == S.NOT_ENFORCED,
    )
    check(
        "' length ' is not the exact value",
        _aggregate(three(finish=" length ")).state == S.NOT_ENFORCED,
    )
    check("'STOP' is not exact stop", _aggregate(three(finish="STOP")).state == S.NOT_ENFORCED)
    check("' stop' is not exact stop", _aggregate(three(finish=" stop")).state == S.NOT_ENFORCED)
    check(
        "content_filter is CONTENT_FILTERED",
        _aggregate(three(finish="content_filter")).state == S.CONTENT_FILTERED,
    )
    check(
        "'Content_Filter' is not exact",
        _aggregate(three(finish="Content_Filter")).state == S.NOT_ENFORCED,
    )
    check(
        "a non-member trial is NOT_ENFORCED",
        _aggregate([_trial(), _trial(member=False), _trial()]).state == S.NOT_ENFORCED,
    )
    check(
        "non-member outranks missing usage",
        _aggregate(three(member=False, tokens=None)).state == S.NOT_ENFORCED,
    )
    check(
        "one missing usage is USAGE_MISSING",
        _aggregate([_trial(), _trial(tokens=None), _trial()]).state == S.USAGE_MISSING,
    )
    partial = _aggregate([_trial(), _trial(tokens=None), _trial()])
    check(
        "V18: usage missing on one trial reports no max claim from the others",
        partial.max_completion_tokens_observed is None,
    )
    check(
        "V18: usage missing on one trial reports no mean claim from the others",
        partial.mean_completion_tokens is None,
    )
    # USAGE-IMPOSSIBLE-COUNT: every trial already proved it answered in-enum with finish_reason
    # "stop" by the time these checks run, so a reported token count that is negative, or zero
    # for that non-empty answer, is a gateway lie -- never a real cheap-output metric.
    check(
        "a negative completion-token count is MALFORMED_RESPONSE, not a real count",
        _aggregate(three(tokens=-5)).state == S.MALFORMED_RESPONSE,
    )
    check(
        "one negative-count trial outranks the others' valid usage",
        _aggregate([_trial(), _trial(tokens=-1), _trial()]).state == S.MALFORMED_RESPONSE,
    )
    zero_all = _aggregate(three(tokens=0))
    check(
        "zero completion tokens for a non-empty answer is USAGE_MISSING, not a real count",
        zero_all.state == S.USAGE_MISSING,
    )
    check(
        "zero-token USAGE_MISSING reports no max claim (never a fake 0)",
        zero_all.max_completion_tokens_observed is None,
    )
    check(
        "zero-token USAGE_MISSING reports no mean claim (never a fake 0)",
        zero_all.mean_completion_tokens is None,
    )
    check(
        "one zero-count trial among otherwise-valid usage is still USAGE_MISSING",
        _aggregate([_trial(), _trial(tokens=0), _trial()]).state == S.USAGE_MISSING,
    )


def test_aggregate_requires_exactly_trials_count() -> None:
    """MUTATION-KILL-BY-TEXT-GUARD: _aggregate_trials refuses to be called with any count
    other than _TRIALS, so a future change that lets the loop call it early (or extra) is
    caught here, not by a model that happens to behave the same every time."""
    for count in (0, 1, 2, probe_module._TRIALS + 1):
        raised = False
        try:
            probe_module._aggregate_trials(
                tuple(_trial() for _ in range(count)), ["m"] * count, datetime.now(UTC)
            )
        except ValueError:
            raised = True
        check(f"_aggregate_trials refuses {count} trials (expected {probe_module._TRIALS})", raised)
    check(
        "_aggregate_trials accepts exactly _TRIALS trials",
        _aggregate([_trial()] * probe_module._TRIALS).state == S.CONSTRAINED_OBSERVED,
    )


def test_aggregate_identity_needs_one_common_resolved_model() -> None:
    ok = _aggregate([_trial()] * 3, ["m", "m", "m"])
    check(
        "one common model is VERIFIED",
        ok.route_identity_state == RouteIdentityState.VERIFIED and ok.resolved_model == "m",
    )
    none = _aggregate([_trial()] * 3, [None, None, None])
    check(
        "no resolved model is UNVERIFIED",
        none.route_identity_state == RouteIdentityState.UNVERIFIED and none.resolved_model is None,
    )
    mixed = _aggregate([_trial()] * 3, ["a", "a", "b"])
    check(
        "two different models are UNVERIFIED",
        mixed.route_identity_state == RouteIdentityState.UNVERIFIED
        and mixed.resolved_model is None,
    )
    part = _aggregate([_trial()] * 3, ["a", None, "a"])
    check(
        "a trial without a model is UNVERIFIED",
        part.route_identity_state == RouteIdentityState.UNVERIFIED,
    )


def test_trial_body_parser_is_strict() -> None:
    def body(**over: object) -> bytes:
        choice: dict[str, Any] = {"message": {"content": "{}"}, "finish_reason": "stop"}
        payload: dict[str, Any] = {
            "choices": [choice],
            "model": "m",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        for key, value in over.items():
            if key == "content":
                choice["message"] = {"content": value}
            elif key == "finish":
                choice["finish_reason"] = value
            else:
                payload[key] = value
        return json.dumps(payload).encode()

    parse = probe_module._parse_trial_body
    check("a valid body parses", parse(body()) is not None)
    check("numeric content is malformed", parse(body(content=5)) is None)
    check("missing finish_reason is malformed", parse(body(finish=None)) is None)
    check("empty finish_reason is malformed", parse(body(finish="")) is None)
    check("numeric finish_reason is malformed", parse(body(finish=3)) is None)
    check("non-JSON is malformed", parse(b"nope") is None)
    check("non-object is malformed", parse(b"[]") is None)
    check("empty choices is malformed", parse(b'{"choices": []}') is None)
    parsed = parse(body(model=123))
    check("a non-string model is no resolved model", parsed is not None and parsed[2] is None)
    parsed = parse(body(model=""))
    check("an empty model is no resolved model", parsed is not None and parsed[2] is None)
    parsed = parse(body(model="gpt-x"))
    check("a string model is the resolved model", parsed is not None and parsed[2] == "gpt-x")
    parsed = parse(body(usage="x"))
    check("unparseable usage is None, not zero", parsed is not None and parsed[3] is None)


def test_guard_failures_and_rejections_classify_exactly() -> None:
    def failure(cause: BaseException | None) -> S:
        exc = OutboundGuardError("x")
        exc.__cause__ = cause
        return probe_module._classify_guard_failure(exc)

    check("dns", failure(socket.gaierror()) == S.DNS_FAILED)
    check("tls", failure(ssl.SSLError()) == S.CERTIFICATE_ERROR)
    check("timeout", failure(TimeoutError()) == S.TIMEOUT)
    check("refused", failure(ConnectionRefusedError()) == S.CONNECTION_REFUSED)
    check("other", failure(OSError()) == S.UNREACHABLE)
    check("no cause", failure(None) == S.UNREACHABLE)

    def reject(error: object) -> S:
        return probe_module._classify_rejection(json.dumps({"error": error}).encode())[0]

    check("filter by code", reject({"code": "content_filter"}) == S.CONTENT_FILTERED)
    check(
        "filter by policy code", reject({"code": "content_policy_violation"}) == S.CONTENT_FILTERED
    )
    check("filter by type alone", reject({"type": "content_filter"}) == S.CONTENT_FILTERED)
    check("param is unsupported", reject({"param": "response_format"}) == S.UNSUPPORTED_PARAMETER)
    check(
        "unsupported code alone",
        reject({"code": "unsupported_parameter"}) == S.UNSUPPORTED_PARAMETER,
    )
    check("unknown code alone", reject({"code": "unknown_parameter"}) == S.UNSUPPORTED_PARAMETER)
    check(
        "unrecognised error is malformed", reject({"code": "rate_limited"}) == S.MALFORMED_RESPONSE
    )
    check("empty error is malformed", reject({}) == S.MALFORMED_RESPONSE)
    check(
        "case-folded filter code is not matched",
        reject({"code": " Content_Filter "}) == S.MALFORMED_RESPONSE,
    )
    check(
        "case-folded filter type is not matched",
        reject({"type": " Content_Filter "}) == S.MALFORMED_RESPONSE,
    )
    check(
        "case-folded unsupported code is not matched",
        reject({"code": "Unsupported_Parameter"}) == S.MALFORMED_RESPONSE,
    )
    check("non-object error is malformed", reject("boom") == S.MALFORMED_RESPONSE)
    check(
        "non-JSON rejection is malformed",
        probe_module._classify_rejection(b"<html>")[0] == S.MALFORMED_RESPONSE,
    )
    check(
        "a filter code outranks a param",
        reject({"code": "content_filter", "param": "x"}) == S.CONTENT_FILTERED,
    )


def test_secret_resolution_edges_and_authorization_are_exact() -> None:
    resolve = probe_module._resolve_inference_key
    when = datetime.now(UTC)
    key, state, detail = resolve("vault://x", secret_config=None, now=when)
    check(
        "a foreign scheme is UNSUPPORTED even with no config",
        key is None and state == S.SECRET_BACKEND_UNSUPPORTED and "scheme" in detail,
    )
    key, state, _ = resolve(
        "openbao://secret/litellm/org1/api_key", secret_config=_SECRET_CONFIG, now=when
    )
    check("a real reference resolves", key == _SECRET_VALUE and state is None)
    for model_id, approved in (
        ("ORG", {"org"}),
        (" org", {"org"}),
        ("org", {"ORG"}),
        ("org ", {"org"}),
    ):
        raised = False
        try:
            probe_constrained_choice(
                base_url="https://127.0.0.1:9",
                model_id=model_id,
                approved_model_ids=frozenset(approved),
                secret_ref=_SECRET_REF,
                secret_config=_SECRET_CONFIG,
                policy=_policy_for(9),
            )
        except ChatProbeNotAuthorizedError:
            raised = True
        check(f"approval is exact: {model_id!r} vs {sorted(approved)}", raised)
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    result = probe_constrained_choice(
        base_url="https://127.0.0.1:9",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://x",
        secret_config=None,
        policy=_policy_for(9),
        now=fixed,
    )
    check("an injected now is the recorded probed_at", result.probed_at == fixed)
    check(
        "the failure detail reaches the result",
        result.detail == "no secret backend is configured for this reference's scheme",
    )
    result = probe_constrained_choice(
        base_url="https://127.0.0.1:9",
        model_id="m",
        approved_model_ids=frozenset({"m"}),
        secret_ref="vault://x",
        secret_config=None,
        policy=_policy_for(9),
    )
    check(
        "the default probed_at is timezone-aware and current",
        result.probed_at.tzinfo is not None
        and abs((datetime.now(UTC) - result.probed_at).total_seconds()) < 60,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0213-S1 constrained-choice probe: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0213-S1 constrained-choice probe: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
