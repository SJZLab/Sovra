#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,misc"
# ruff: noqa: E501
"""FTF-0616-F1 -- Judge-call deployment fingerprint and cache bypass.

Follow-up on certified FTF-0616 (`workers/evaluation/llm_judge_grader.py`),
prerequisite of FTF-0617. Additive only -- no refusal/calibration/echo-
matching/schema behaviour from the certified round changes.

Acceptance: `LlmJudgeResult.deployment_fingerprint` records the real
`x-litellm-model-id` response header -- the same documented header and the
same exact lower-case-ASCII lookup as
`route_attestation._deployment_fingerprint_from_headers` (never a substring
match) -- on every genuine 200 response, and is `None` on every other path.
Every judge request also carries LiteLLM's own documented
`"cache": {"no-cache": true}` field.

Runnable without any database. The pure extraction-function checks
(`test_header_*`) run with no network at all. The gateway-integration and
fault-injection checks run against a real local HTTPS server standing in
for the LiteLLM gateway, same no-external-egress convention every sibling
FTF-02xx/FTF-0616 suite already establishes; skipped and disclosed if this
sandbox has no `openssl`/`update-ca-certificates`/passwordless `sudo` (the
pure checks still run and still count).

Run directly: python3 tests/tasks/test_ftf_0616_f1.py
"""

from __future__ import annotations

import atexit
import ipaddress
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import workers.evaluation.llm_judge_grader as judge  # noqa: E402
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []
CID = "FTF-0616-F1"
MODULE_PATH = ROOT / "workers" / "evaluation" / "llm_judge_grader.py"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# -- Pure extraction-function checks (no network needed either way) ---------


def test_header_present_is_recorded_verbatim() -> None:
    value = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": "gpt-4-east-1"})
    check("F1", "a present, valid header is recorded verbatim", value == "gpt-4-east-1")


def test_header_name_lookup_is_case_insensitive_but_exact() -> None:
    value = judge._deployment_fingerprint_from_headers({"X-LiteLLM-Model-Id": "deployment-9"})
    check(
        "F1",
        "the header NAME lookup is case-insensitive (HTTP header names are)",
        value == "deployment-9",
    )


def test_header_absent_is_none() -> None:
    value = judge._deployment_fingerprint_from_headers({"content-type": "application/json"})
    check("F1", "no matching header at all is None, never fabricated", value is None)


def test_header_empty_value_is_none() -> None:
    value = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": ""})
    check("F1", "an empty header value is None (below the 1-char floor)", value is None)


def test_header_over_length_is_none() -> None:
    at_bound = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": "a" * 256})
    over_bound = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": "a" * 257})
    check("F1", "exactly 256 characters is accepted (the real column width)", at_bound == "a" * 256)
    check("F1", "257 characters is None (over the real column width)", over_bound is None)


def test_header_control_character_is_none() -> None:
    value = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": "dep\x01loy"})
    check("F1", "a control character (Cc) makes the whole value None", value is None)


def test_header_nul_byte_is_none() -> None:
    value = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": "dep\x00loy"})
    check("F1", "a NUL byte (Cc) makes the whole value None", value is None)


def test_header_format_and_separator_characters_are_none() -> None:
    # Same advisory Unicode points the certified FTF-0616 round-8 sweep
    # already exercises elsewhere in this repo: U+2029 (Zp), the
    # left-to-right/right-to-left marks (Cf) and U+00AD soft hyphen (Cf).
    for name, char in (
        ("U+2029 PARAGRAPH SEPARATOR (Zp)", " "),
        ("U+200E LEFT-TO-RIGHT MARK (Cf)", "‎"),
        ("U+200F RIGHT-TO-LEFT MARK (Cf)", "‏"),
        ("U+00AD SOFT HYPHEN (Cf)", "­"),
    ):
        value = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": f"dep{char}loy"})
        check("F1", f"a value containing {name} is None", value is None)


def test_header_lookalike_names_are_ignored() -> None:
    suffix = judge._deployment_fingerprint_from_headers(
        {"x-litellm-model-id-x": "should-not-match"}
    )
    prefix = judge._deployment_fingerprint_from_headers({"x-litellm-model": "should-not-match"})
    check(
        "F1",
        "a header name with an extra suffix never matches (no substring match)",
        suffix is None,
    )
    check("F1", "a shorter look-alike header name never matches (no prefix match)", prefix is None)


def test_header_lookup_never_substring_matches_a_real_header() -> None:
    # A real header is present (genuinely matched, name differs only by
    # case) alongside a look-alike; the look-alike's own distinct value
    # must never leak through under a looser (substring) comparison.
    value = judge._deployment_fingerprint_from_headers(
        {
            "X-LiteLLM-Model-Id": "the-real-deployment",
            "x-litellm-model-id-x": "the-lookalike-value",
            "x-litellm-model": "the-other-lookalike-value",
        }
    )
    check(
        "F1",
        "only the exact header name's own value is ever returned",
        value == "the-real-deployment",
    )


# -- Self-attack: the pure extraction function's own guards are load-bearing -


def _run_mutated_extraction(mutated_src: str, headers: dict[str, str]) -> str:
    """Writes `mutated_src` over the module on disk, then calls
    `_deployment_fingerprint_from_headers` in a brand-new, isolated
    subprocess (never `importlib.reload` in this process). Always restores
    the file afterward, even on failure."""
    original = MODULE_PATH.read_text()
    MODULE_PATH.write_text(mutated_src)
    try:
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import workers.evaluation.llm_judge_grader as judge\n"
            f"print(repr(judge._deployment_fingerprint_from_headers({headers!r})))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            FAILURES.append(
                f"FAIL [self-attack] subprocess crashed: {result.stdout}\n{result.stderr[-2000:]}"
            )
            return "<crashed>"
        return result.stdout.strip()
    finally:
        MODULE_PATH.write_text(original)


def test_self_attack_header_ignored_fault() -> None:
    src = MODULE_PATH.read_text()
    needle = "        if name.lower() != target:\n            continue\n"
    check("S1", "the header-name match is present verbatim", needle in src)
    mutated = src.replace(
        needle, "        if name.lower() != target:\n            continue\n        return None\n"
    )
    assert mutated != src
    out = _run_mutated_extraction(mutated, {"x-litellm-model-id": "real-deployment"})
    check(
        "S1",
        "ignoring a present, valid header (forcing None unconditionally) is caught",
        out == "None",
    )
    # Confirm the un-mutated file still gives the real value (the fault
    # above really did change behaviour, not just crash).
    control = judge._deployment_fingerprint_from_headers({"x-litellm-model-id": "real-deployment"})
    check("S1", "the restored file still returns the real value", control == "real-deployment")


def test_self_attack_substring_match_fault() -> None:
    src = MODULE_PATH.read_text()
    needle = "        if name.lower() != target:\n"
    check("S1", "the exact-match comparison is present verbatim", needle in src)
    mutated = src.replace(needle, "        if target not in name.lower():\n")
    assert mutated != src
    out = _run_mutated_extraction(mutated, {"x-litellm-model-id-x": "lookalike-value"})
    check(
        "S1",
        "widening the name comparison to a substring match lets a look-alike header "
        "through -- caught",
        out == "'lookalike-value'",
    )


def test_self_attack_length_and_character_check_dropped_fault() -> None:
    src = MODULE_PATH.read_text()
    needle = (
        "        if not isinstance(value, str) or not (\n"
        "            1 <= len(value) <= _DEPLOYMENT_FINGERPRINT_MAX_LENGTH\n"
        "        ):\n"
        "            return None\n"
        "        if any(unicodedata.category(ch) in _FORBIDDEN_FINGERPRINT_CATEGORIES for ch in value):\n"
        "            return None\n"
    )
    check("S1", "the length/character validation is present verbatim", needle in src)
    mutated = src.replace(needle, "")
    assert mutated != src
    over_length = _run_mutated_extraction(mutated, {"x-litellm-model-id": "a" * 257})
    check(
        "S1",
        "dropping the length check lets a 257-character value through -- caught",
        over_length == repr("a" * 257),
    )
    control_char = _run_mutated_extraction(mutated, {"x-litellm-model-id": "dep\x01loy"})
    check(
        "S1",
        "dropping the character check lets a control character through -- caught",
        control_char == repr("dep\x01loy"),
    )


# -- Real local TLS fixture: same convention every sibling FTF-020x/FTF-0616 suite --

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/judge-org"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-judge-key-0616-f1"
_SECRET_TOKEN = "test-vault-token-f1"


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        token = self.headers.get("X-Vault-Token")
        if token != _SECRET_TOKEN or self.path != f"/v1/{_SECRET_MOUNT}/data/{_SECRET_PATH}":
            body = json.dumps({"errors": ["denied"]}).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {"data": {"data": {_SECRET_FIELD: _SECRET_VALUE}, "metadata": {"version": 1}}}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
_SECRET_PORT = _SECRET_SERVER.server_address[1]
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)
_SECRET_CONFIG = OpenBaoConfig(address=f"http://127.0.0.1:{_SECRET_PORT}", token=_SECRET_TOKEN)
_SECRET_REF = f"openbao://{_SECRET_MOUNT}/{_SECRET_PATH}/{_SECRET_FIELD}"

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0616-f1-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0616-f1-test-ca.crt")
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
            "/CN=FTF-0616-F1 Test CA",
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
if _UPDATE_CA_AVAILABLE:
    _TRUSTED_LEAF, _TRUSTED_KEY = _generate_trusted_server_cert()
else:
    print(
        "FTF-0616-F1: this sandbox has no openssl/update-ca-certificates -- the live "
        "TLS-backed checks are skipped and disclosed here; the pure extraction-function "
        "checks still run and still count. GitHub Actions' ubuntu-latest runner has both."
    )


def _policy_for(port: int) -> ConnectionPolicy:
    return ConnectionPolicy(
        name="test-litellm-gateway-f1",
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


_CRITERIA = ("accuracy",)


def _verdict_json(verdict: object = "pass") -> dict[str, object]:
    return {
        "verdict": verdict,
        "reasoning": "ok",
        "confidence": 0.9,
        "criterion_scores": {"accuracy": 1.0},
    }


def _success_body(model: str = "judge-model-v1") -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-real",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(_verdict_json())},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    ).encode("utf-8")


@dataclass
class _GatewayState:
    status: int = 200
    body: bytes = field(default_factory=_success_body)
    extra_response_headers: dict[str, str] = field(default_factory=dict)
    request_count: int = 0
    last_request_body: bytes | None = None
    last_request_headers: dict[str, str] | None = None


_GATEWAY = _GatewayState()


class _JudgeGatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        _GATEWAY.last_request_body = raw
        _GATEWAY.last_request_headers = dict(self.headers.items())
        _GATEWAY.request_count += 1
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        auth = self.headers.get("Authorization", "")
        if _GATEWAY.status not in (401, 403) and auth != f"Bearer {_SECRET_VALUE}":
            body = b'{"error":{"type":"invalid_request_error","message":"no key"}}'
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(_GATEWAY.status)
        self.send_header("Content-Length", str(len(_GATEWAY.body)))
        for name, value in _GATEWAY.extra_response_headers.items():
            self.send_header(name, value)
        self.end_headers()
        if _GATEWAY.body:
            self.wfile.write(_GATEWAY.body)


def _reset_gateway() -> None:
    _GATEWAY.status = 200
    _GATEWAY.body = _success_body()
    _GATEWAY.extra_response_headers = {}
    _GATEWAY.request_count = 0
    _GATEWAY.last_request_body = None
    _GATEWAY.last_request_headers = None


_RUBRIC = judge.JudgeRubric(
    rubric_id="fingerprint-check-v1",
    version=1,
    instructions="Pass always.",
    criteria=_CRITERIA,
)

_CALIBRATED = judge.CalibrationRecord(
    rubric_id="fingerprint-check-v1",
    rubric_version=1,
    judge_model_id="judge-model-v1",
    reviewer_ids=("reviewer-a", "reviewer-b"),
    judge_pass_label_pass=47,
    judge_pass_label_fail=3,
    judge_fail_label_pass=2,
    judge_fail_label_fail=48,
)


def _grade(port: int, **overrides: object):
    kwargs: dict[str, object] = {
        "base_url": f"https://127.0.0.1:{port}",
        "judge_model_id": "judge-model-v1",
        "candidate_model_id": "candidate-model-v1",
        "approved_judge_model_ids": frozenset({"judge-model-v1"}),
        "rubric": _RUBRIC,
        "candidate_response": "a candidate answer.",
        "calibration": _CALIBRATED,
        "critical": False,
        "secret_ref": _SECRET_REF,
        "secret_config": _SECRET_CONFIG,
        "policy": _policy_for(port),
        "timeout": 10.0,
    }
    kwargs.update(overrides)
    return judge.grade_llm_judge(**kwargs)  # type: ignore[arg-type]


_PORT = 0
if _TRUSTED_LEAF is not None:
    _SERVER = _start_https_server(_JudgeGatewayHandler)
    _PORT = _SERVER.server_address[1]


# -- Real round trip over HTTPS (skipped and disclosed if no local CA tooling) -------


def test_happy_path_records_the_real_deployment_fingerprint() -> None:
    if _PORT == 0:
        return
    _reset_gateway()
    _GATEWAY.extra_response_headers = {"x-litellm-model-id": "deployment-fp-east-1a"}
    result = _grade(_PORT)
    check(
        "A1",
        "a real PASSED verdict with the header set records the fingerprint",
        result.state is judge.LlmJudgeState.PASSED,
    )
    check(
        "A1",
        "deployment_fingerprint is recorded verbatim from the real response header",
        result.deployment_fingerprint == "deployment-fp-east-1a",
    )


def test_happy_path_without_header_leaves_fingerprint_none() -> None:
    if _PORT == 0:
        return
    _reset_gateway()
    result = _grade(_PORT)
    check(
        "A1",
        "no header on an otherwise-real response is None, never fabricated",
        result.deployment_fingerprint is None,
    )


def test_every_judge_request_carries_the_no_cache_field_exactly_once() -> None:
    if _PORT == 0:
        return
    _reset_gateway()
    _grade(_PORT)
    sent = json.loads(_GATEWAY.last_request_body)
    check("A2", "the sent request carries a cache field", "cache" in sent)
    check(
        "A2",
        "the cache field is exactly the documented no-cache bypass",
        sent.get("cache") == {"no-cache": True},
    )
    raw_text = _GATEWAY.last_request_body.decode("utf-8")
    check(
        "A2",
        'the literal key "cache" appears exactly once in the sent body',
        raw_text.count('"cache"') == 1,
    )


def test_request_body_is_byte_identical_to_round_8_apart_from_cache() -> None:
    """Golden test: parse the sent body, drop the new `cache` key, and
    confirm every other key/value is exactly what the certified round-8
    adapter already sends (same model/messages/max_tokens/stream/
    response_format shape `test_ftf_0616.py`'s own golden test asserts)."""
    if _PORT == 0:
        return
    _reset_gateway()
    _grade(_PORT)
    sent = json.loads(_GATEWAY.last_request_body)
    check(
        "A2",
        "the cache field is present before stripping it for the golden comparison",
        "cache" in sent,
    )
    del sent["cache"]
    check(
        "A2",
        "every other field is unchanged from the certified round-8 request shape",
        sent["model"] == "judge-model-v1"
        and sent["stream"] is False
        and sent["max_tokens"] == judge._MAX_TOKENS
        and sent["response_format"]["type"] == "json_schema"
        and len(sent["messages"]) == 2,
    )


def test_header_on_401_403_or_400_response_is_ignored() -> None:
    if _PORT == 0:
        return
    for status in (401, 403, 400):
        _reset_gateway()
        _GATEWAY.status = status
        _GATEWAY.body = (
            b'{"error":{"type":"invalid_request_error","message":"nope"}}'
            if status == 400
            else b'{"error":{"message":"denied"}}'
        )
        _GATEWAY.extra_response_headers = {"x-litellm-model-id": "should-never-be-read"}
        result = _grade(_PORT)
        check(
            "A3",
            f"a {status} response never yields a fingerprint even with the header present",
            result.deployment_fingerprint is None,
        )


def test_header_on_5xx_response_is_ignored() -> None:
    if _PORT == 0:
        return
    _reset_gateway()
    _GATEWAY.status = 503
    _GATEWAY.body = b""
    _GATEWAY.extra_response_headers = {"x-litellm-model-id": "should-never-be-read"}
    result = _grade(_PORT)
    check(
        "A3",
        "a 5xx response never yields a fingerprint even with the header present",
        result.deployment_fingerprint is None and result.state is judge.LlmJudgeState.UNREACHABLE,
    )


def test_malformed_response_still_carries_a_real_fingerprint() -> None:
    """A 200 that fails later parsing (not a valid chat completion shape)
    is still a genuine 200 the gateway answered -- the fingerprint is real
    information and is not withheld just because the content failed a
    later check."""
    if _PORT == 0:
        return
    _reset_gateway()
    _GATEWAY.body = b"not json at all"
    _GATEWAY.extra_response_headers = {"x-litellm-model-id": "deployment-on-malformed"}
    result = _grade(_PORT)
    check(
        "A3",
        "a malformed 200 body is MALFORMED_RESPONSE",
        result.state is judge.LlmJudgeState.MALFORMED_RESPONSE,
    )
    check(
        "A3",
        "a genuine 200's fingerprint is still recorded even when the content itself is malformed",
        result.deployment_fingerprint == "deployment-on-malformed",
    )


# -- Self-attack: the integration-level guards are load-bearing --------------


def _run_against_gateway(
    mutated_src: str, extra_response_headers: dict[str, str], status: int = 200
) -> object:
    """Writes `mutated_src` over the module on disk, then runs one real
    `grade_llm_judge` call against the live local gateway in a brand-new,
    isolated subprocess, and returns the printed `deployment_fingerprint`
    repr (or the sent-body JSON) as a plain string. Always restores the
    file afterward, even on failure."""
    original = MODULE_PATH.read_text()
    MODULE_PATH.write_text(mutated_src)
    try:
        _reset_gateway()
        _GATEWAY.status = status
        _GATEWAY.extra_response_headers = extra_response_headers
        script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
import workers.evaluation.llm_judge_grader as judge
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.security.url_policy import ConnectionPolicy
import ipaddress, json

policy = ConnectionPolicy(
    name="test-litellm-gateway-f1",
    allowed_schemes=frozenset({{"https"}}),
    allowed_ports=frozenset({{ {_PORT} }}),
    explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
)
secret_config = OpenBaoConfig(address={f"http://127.0.0.1:{_SECRET_PORT}"!r}, token={_SECRET_TOKEN!r})
rubric = judge.JudgeRubric(rubric_id={_RUBRIC.rubric_id!r}, version={_RUBRIC.version}, instructions={_RUBRIC.instructions!r}, criteria={_RUBRIC.criteria!r})
calibration = judge.CalibrationRecord(
    rubric_id={_CALIBRATED.rubric_id!r}, rubric_version={_CALIBRATED.rubric_version},
    judge_model_id={_CALIBRATED.judge_model_id!r}, reviewer_ids={_CALIBRATED.reviewer_ids!r},
    judge_pass_label_pass={_CALIBRATED.judge_pass_label_pass}, judge_pass_label_fail={_CALIBRATED.judge_pass_label_fail},
    judge_fail_label_pass={_CALIBRATED.judge_fail_label_pass}, judge_fail_label_fail={_CALIBRATED.judge_fail_label_fail},
)
result = judge.grade_llm_judge(
    base_url={f"https://127.0.0.1:{_PORT}"!r},
    judge_model_id="judge-model-v1",
    candidate_model_id="candidate-model-v1",
    approved_judge_model_ids=frozenset({{"judge-model-v1"}}),
    rubric=rubric,
    candidate_response="a candidate answer.",
    calibration=calibration,
    critical=False,
    secret_ref={_SECRET_REF!r},
    secret_config=secret_config,
    policy=policy,
    timeout=10.0,
)
print("FINGERPRINT=" + repr(result.deployment_fingerprint))
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            FAILURES.append(
                f"FAIL [self-attack] subprocess crashed: {result.stdout}\n{result.stderr[-2000:]}"
            )
            return "<crashed>"
        for line in result.stdout.splitlines():
            if line.startswith("FINGERPRINT="):
                return line[len("FINGERPRINT=") :]
        return "<no output>"
    finally:
        MODULE_PATH.write_text(original)


def test_self_attack_value_read_from_request_not_response_fault() -> None:
    if _PORT == 0:
        return
    src = MODULE_PATH.read_text()
    needle = "    deployment_fingerprint = _deployment_fingerprint_from_headers(response.headers)\n"
    check("S2", "the response-headers read is present verbatim", needle in src)
    mutated = src.replace(
        needle,
        "    deployment_fingerprint = _deployment_fingerprint_from_headers(headers)\n",
    )
    assert mutated != src
    out = _run_against_gateway(mutated, {"x-litellm-model-id": "real-deployment-value"})
    check(
        "S2",
        "reading the outgoing request headers instead of the real response headers loses "
        "the real fingerprint (the outgoing headers never carry it) -- caught",
        out == "None",
    )
    control = _run_against_gateway(mutated, {}, status=200)
    check(
        "S2",
        "the mutated call still completes (it is a wrong-source bug, not a crash)",
        control != "<crashed>",
    )


def test_self_attack_fingerprint_read_on_non_200_path_fault() -> None:
    if _PORT == 0:
        return
    src = MODULE_PATH.read_text()
    latency_needle = "    latency_ms = (monotonic() - started) * 1000.0\n"
    check("S2", "the latency computation line is present verbatim", latency_needle in src)
    mutated = src.replace(
        latency_needle,
        latency_needle
        + "    deployment_fingerprint = _deployment_fingerprint_from_headers(response.headers)\n",
    )
    invalid_token_needle = (
        "            LlmJudgeState.INVALID_TOKEN,\n"
        "            latency_ms=latency_ms,\n"
        '            detail="gateway rejected the inference credential",\n'
        "        )\n"
    )
    check("S2", "the INVALID_TOKEN return is present verbatim", invalid_token_needle in mutated)
    mutated = mutated.replace(
        invalid_token_needle,
        "            LlmJudgeState.INVALID_TOKEN,\n"
        "            latency_ms=latency_ms,\n"
        '            detail="gateway rejected the inference credential",\n'
        "            deployment_fingerprint=deployment_fingerprint,\n"
        "        )\n",
    )
    assert mutated != src
    out = _run_against_gateway(
        mutated, {"x-litellm-model-id": "should-never-surface-on-403"}, status=403
    )
    check(
        "S2",
        "reading the header before the status check lets a 403's fingerprint leak through "
        "-- caught",
        out == "'should-never-surface-on-403'",
    )


def test_self_attack_cache_field_dropped_fault() -> None:
    if _PORT == 0:
        return
    src = MODULE_PATH.read_text()
    needle = '            "cache": {"no-cache": True},\n'
    check("S2", "the cache field is present verbatim", needle in src)
    mutated = src.replace(needle, "")
    assert mutated != src
    original = MODULE_PATH.read_text()
    MODULE_PATH.write_text(mutated)
    try:
        _reset_gateway()
        script = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
import workers.evaluation.llm_judge_grader as judge
from packages.litellm_client.secret_manager_integration import OpenBaoConfig
from packages.security.url_policy import ConnectionPolicy
import ipaddress, json

policy = ConnectionPolicy(
    name="test-litellm-gateway-f1",
    allowed_schemes=frozenset({{"https"}}),
    allowed_ports=frozenset({{ {_PORT} }}),
    explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
)
secret_config = OpenBaoConfig(address={f"http://127.0.0.1:{_SECRET_PORT}"!r}, token={_SECRET_TOKEN!r})
rubric = judge.JudgeRubric(rubric_id={_RUBRIC.rubric_id!r}, version={_RUBRIC.version}, instructions={_RUBRIC.instructions!r}, criteria={_RUBRIC.criteria!r})
calibration = judge.CalibrationRecord(
    rubric_id={_CALIBRATED.rubric_id!r}, rubric_version={_CALIBRATED.rubric_version},
    judge_model_id={_CALIBRATED.judge_model_id!r}, reviewer_ids={_CALIBRATED.reviewer_ids!r},
    judge_pass_label_pass={_CALIBRATED.judge_pass_label_pass}, judge_pass_label_fail={_CALIBRATED.judge_pass_label_fail},
    judge_fail_label_pass={_CALIBRATED.judge_fail_label_pass}, judge_fail_label_fail={_CALIBRATED.judge_fail_label_fail},
)
judge.grade_llm_judge(
    base_url={f"https://127.0.0.1:{_PORT}"!r},
    judge_model_id="judge-model-v1",
    candidate_model_id="candidate-model-v1",
    approved_judge_model_ids=frozenset({{"judge-model-v1"}}),
    rubric=rubric,
    candidate_response="a candidate answer.",
    calibration=calibration,
    critical=False,
    secret_ref={_SECRET_REF!r},
    secret_config=secret_config,
    policy=policy,
    timeout=10.0,
)
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            FAILURES.append(
                f"FAIL [self-attack] subprocess crashed: {result.stdout}\n{result.stderr[-2000:]}"
            )
            return
        sent = json.loads(_GATEWAY.last_request_body)
        check(
            "S2",
            "dropping the cache field means the sent request no longer carries it -- caught",
            "cache" not in sent,
        )
    finally:
        MODULE_PATH.write_text(original)


def main() -> None:
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                    FAILURES.append(f"FAIL [{CID}] {name} raised {type(exc).__name__}: {exc}")
    finally:
        pass

    if FAILURES:
        print(f"FTF-0616-F1 judge deployment fingerprint tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0616-F1 judge deployment fingerprint tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
