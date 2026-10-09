#!/usr/bin/env python3
# mypy: disable-error-code="arg-type,attr-defined,return-value,assignment,call-arg,index,union-attr,misc"
# ruff: noqa: E501
"""FTF-0616-F1 -- judge-call deployment fingerprint and cache bypass.

Follow-up on certified FTF-0616 (QUEUE.md Backlog, Control-plane design
2026-10-09). Runs on the existing FTF-0616 local TLS gateway harness
(`tests/tasks/test_ftf_0616.py`): a real local HTTPS server stands in for the
organization's LiteLLM gateway, so every assertion below is about bytes that
really crossed the wire or headers a real response really carried.

Acceptance (additive only):
  * `LlmJudgeResult.deployment_fingerprint` is the judge response's
    `x-litellm-model-id` header: exact `str`, 1..256 characters, no
    Cc/Cf/Zl/Zp character, ASCII only; otherwise `None`. Never fabricated and
    `None` on every non-200 path.
  * every judge request carries `"cache": {"no-cache": true}` exactly once and
    is otherwise byte-identical to round 8.

Run directly: python3 tests/tasks/test_ftf_0616_f1.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import packages.litellm_client.route_attestation as attestation  # noqa: E402
import test_ftf_0616 as h  # noqa: E402  (the certified FTF-0616 harness, reused verbatim)

judge = h.judge
FAILURES: list[str] = []
MODULE_PATH = h.MODULE_PATH
_HEADER = "x-litellm-model-id"
_PORT = 0

# `scripts/mutation_gate.py` edits the real module in place and re-runs this file once per
# mutant; the on-disk fault-injection tests below would race that, so they step aside there.
UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
NO_FAULTS = os.environ.get("FTF0616_F1_NO_FAULTS") == "1" or UNDER_MUTATION_GATE


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_EXTRA_HEADERS: list[tuple[str, str]] = []


class _FingerprintGatewayHandler(h._JudgeGatewayHandler):
    """The certified harness gateway, plus whatever response headers a test sets."""

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        for name, value in _EXTRA_HEADERS:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


if h._TRUSTED_LEAF is not None:
    _SERVER = h._start_https_server(_FingerprintGatewayHandler)
    _PORT = _SERVER.server_address[1]


def _grade_with(headers: list[tuple[str, str]], *, status: int = 200):
    h._reset_gateway()
    _EXTRA_HEADERS[:] = headers
    h._GATEWAY.status = status
    try:
        return h._grade(_PORT)
    finally:
        _EXTRA_HEADERS.clear()


def test_the_header_name_is_the_one_route_attestation_reads() -> None:
    check(
        "F1-A0",
        "the fingerprint header is the same documented header route_attestation reads",
        attestation._DEPLOYMENT_FINGERPRINT_HEADER == _HEADER
        and getattr(judge, "_DEPLOYMENT_FINGERPRINT_HEADER", None) == _HEADER,
    )


def test_a_present_header_is_recorded_verbatim() -> None:
    if _PORT == 0:
        return
    result = _grade_with([(_HEADER, "dep-abc123")])
    check(
        "F1-A",
        "a present header is recorded verbatim",
        result.deployment_fingerprint == "dep-abc123",
    )
    check("F1-A", "the verdict is unchanged by the header", result.passed is True)
    longest = "d" * 256
    result = _grade_with([(_HEADER, longest)])
    check("F1-A", "a 256-character value is accepted", result.deployment_fingerprint == longest)
    result = _grade_with([("X-LiteLLM-Model-Id", "dep-mixed-case")])
    check(
        "F1-A",
        "the header name is matched case-insensitively",
        result.deployment_fingerprint == "dep-mixed-case",
    )


def test_a_missing_header_is_none_never_fabricated() -> None:
    if _PORT == 0:
        return
    result = _grade_with([])
    check("F1-B", "an absent header is None", result.deployment_fingerprint is None)
    check("F1-B", "an absent header does not change PASSED here", result.passed is True)
    result = _grade_with([(_HEADER, "")])
    check("F1-B", "an empty header is None", result.deployment_fingerprint is None)
    check("F1-B", "an empty header does not change PASSED here", result.passed is True)


def test_an_unacceptable_value_is_none() -> None:
    if _PORT == 0:
        return
    bad_values = {
        "257 characters": "d" * 257,
        "control character": "dep\x01abc",
        "DEL": "dep\x7fabc",
        "non-ASCII": "dep-é",
        "NUL": "dep\x00abc",
    }
    for label, value in bad_values.items():
        result = _grade_with([(_HEADER, value)])
        check("F1-B", f"{label} is None", result.deployment_fingerprint is None)

    # direct, because a real HTTP parser may refuse some of these before they reach the grader
    def direct(headers: object) -> object:
        # a crash is a wrong answer, reported against the criterion rather than as a bare traceback
        try:
            return judge._deployment_fingerprint_from_headers(headers)
        except Exception as exc:  # noqa: BLE001
            return f"<raised {type(exc).__name__}>"

    for label, value in {
        "line separator U+2028": "dep abc",
        "paragraph separator U+2029": "dep abc",
        "format character U+200B": "dep​abc",
        "soft hyphen U+00AD": "dep­abc",
        "right-to-left mark": "dep‏abc",
        "newline": "dep\nabc",
        "non-str": 12345,
        "bytes": b"dep-abc",
        "None": None,
        "empty": "",
        "257": "d" * 257,
    }.items():
        check("F1-B", f"direct: {label} is None", direct({_HEADER: value}) is None)
    check("F1-B", "direct: 1 character is accepted", direct({_HEADER: "a"}) == "a")
    check("F1-B", "direct: 256 characters are accepted", direct({_HEADER: "a" * 256}) == "a" * 256)
    check("F1-B", "direct: an inner space is accepted", direct({_HEADER: "dep abc"}) == "dep abc")
    check(
        "F1-B",
        "direct: two differently-cased copies of the header never pick a winner",
        direct({"X-LiteLLM-Model-Id": "one", _HEADER: "two"}) is None,
    )
    check("F1-B", "direct: no headers is None", direct({}) is None)

    class _StrSubclass(str):
        pass

    check("F1-B", "direct: a str subclass is None", direct({_HEADER: _StrSubclass("dep")}) is None)
    check(
        "F1-B",
        "direct: headers that are not a dict are None",
        direct([(_HEADER, "a")]) is None and direct(None) is None,
    )
    check(
        "F1-D",
        "direct: a non-str header name is skipped, not a crash",
        direct({1: "x", b"x-litellm-model-id": "y", _HEADER: "dep"}) == "dep",
    )


def test_a_non_200_response_never_carries_a_fingerprint() -> None:
    if _PORT == 0:
        return
    for status in (400, 401, 403, 404, 500, 502, 503):
        result = _grade_with([(_HEADER, "dep-abc123")], status=status)
        check(
            "F1-C",
            f"a {status} response's header is ignored",
            result.deployment_fingerprint is None and result.passed is None,
        )
    # a 2xx that is not a 200 is not a verdict either
    result = _grade_with([(_HEADER, "dep-abc123")], status=201)
    check("F1-C", "a 201 response's header is ignored", result.deployment_fingerprint is None)


def test_look_alike_header_names_are_ignored() -> None:
    if _PORT == 0:
        return
    for name in (
        "x-litellm-model-id-x",
        "x-litellm-model",
        "x-litellm-model-ids",
        "xx-litellm-model-id",
        "x-litellm-model-id ",
        "x_litellm_model_id",
        "x-litellm-response-cost",
    ):
        result = _grade_with([(name, "dep-abc123")])
        check("F1-D", f"look-alike {name!r} is ignored", result.deployment_fingerprint is None)
    check(
        "F1-D",
        "direct: a substring/prefix/suffix name never matches",
        judge._deployment_fingerprint_from_headers({"x-litellm-model-id-x": "a"}) is None
        and judge._deployment_fingerprint_from_headers({"prefix-x-litellm-model-id": "a"}) is None
        and judge._deployment_fingerprint_from_headers({"x-litellm-model": "a"}) is None,
    )
    check(
        "F1-D",
        "direct: a non-ASCII case-fold look-alike never matches",
        judge._deployment_fingerprint_from_headers({"x-lİtellm-model-id": "a"}) is None,
    )


def test_the_fingerprint_is_read_from_the_response_not_the_request() -> None:
    if _PORT == 0:
        return
    result = _grade_with([])
    sent_headers = h._GATEWAY.last_request_body or b""
    check(
        "F1-E",
        "no fingerprint appears when the response carries none, whatever the request held",
        result.deployment_fingerprint is None and b"x-litellm-model-id" not in sent_headers,
    )


def test_a_200_that_is_not_a_verdict_still_records_which_deployment_served_it() -> None:
    if _PORT == 0:
        return
    h._reset_gateway()
    h._GATEWAY.body = h._success_body(h._verdict_json(), model="some-other-model")
    _EXTRA_HEADERS[:] = [(_HEADER, "dep-other")]
    try:
        result = h._grade(_PORT)
    finally:
        _EXTRA_HEADERS.clear()
    check(
        "F1-F",
        "a MODEL_MISMATCH 200 records the serving deployment and stays a mismatch",
        result.state is judge.LlmJudgeState.MODEL_MISMATCH
        and result.deployment_fingerprint == "dep-other",
    )


def test_the_no_cache_field_is_sent_exactly_once() -> None:
    if _PORT == 0:
        return
    h._reset_gateway()
    h._grade(_PORT)
    raw = h._GATEWAY.last_request_body
    pairs: list[list[tuple[str, object]]] = []

    def hook(items: list[tuple[str, object]]) -> dict[str, object]:
        pairs.append(items)
        return dict(items)

    sent = json.loads(raw, object_pairs_hook=hook)
    top_level = [key for key, _ in pairs[-1]]
    check("F1-G", "the top-level `cache` key appears exactly once", top_level.count("cache") == 1)
    check(
        "F1-G",
        "the cache field is exactly {no-cache: true}",
        sent.get("cache") == {"no-cache": True},
    )
    check(
        "F1-G",
        "the golden: round 8 keys in round 8 order, then only `cache`",
        top_level == ["model", "messages", "max_tokens", "stream", "response_format", "cache"],
    )
    round8 = {k: v for k, v in sent.items() if k != "cache"}
    check(
        "F1-G",
        "removing the one cache key leaves the round-8 request byte-for-byte",
        json.dumps(round8).encode("utf-8") == raw.replace(b', "cache": {"no-cache": true}', b"", 1),
    )
    check(
        "F1-G",
        "the whole body is exactly the serialization of what was parsed",
        json.dumps(sent).encode("utf-8") == raw,
    )


# -- Self-attack: break the production code on disk, confirm this file turns RED ---------
# Runs last (`test_zz_`): every child process removes the sandbox CA trust on exit, so no live-TLS
# test may run after the first child.


def _red_under_fault(mutated_src: str, expect: str) -> bool:
    """Writes `mutated_src` over the module, runs this file in a fresh subprocess (fault tests off)
    and reports whether it exited non-zero naming `expect`. Always restores the module."""
    original = MODULE_PATH.read_text()
    MODULE_PATH.write_text(mutated_src)
    try:
        env = dict(os.environ, FTF0616_F1_NO_FAULTS="1")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(ROOT),
        )
        return proc.returncode != 0 and expect in proc.stdout
    finally:
        MODULE_PATH.write_text(original)


FAULTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "F1-A",
        "the header is ignored (helper always None)",
        "    values = [\n",
        "    return None\n    values = [\n",
    ),
    (
        "F1-D",
        "substring match on the header name",
        "name.lower() == _DEPLOYMENT_FINGERPRINT_HEADER",
        "_DEPLOYMENT_FINGERPRINT_HEADER in name.lower()",
    ),
    (
        "F1-D",
        "a non-str header name crashes the lookup",
        "if isinstance(name, str) and name.lower()",
        "if name.lower()",
    ),
    (
        "F1-C",
        "the fingerprint is read on a non-200 path",
        "    if response.status in (401, 403):\n",
        "    make = partial(make, deployment_fingerprint=_deployment_fingerprint_from_headers(response.headers))\n    if response.status in (401, 403):\n",
    ),
    (
        "F1-B",
        "the upper length bound is dropped",
        "len(value) <= _DEPLOYMENT_FINGERPRINT_MAX_LENGTH",
        "len(value) >= 1",
    ),
    ("F1-B", "the empty-value check is dropped", "1 <= len(value) <=", "len(value) <="),
    (
        "F1-B",
        "the character-category check is dropped",
        "all(unicodedata.category(ch) not in _UNSAFE_FINGERPRINT_CATEGORIES for ch in value)",
        "True",
    ),
    ("F1-B", "the ASCII check is dropped", "        and value.isascii()\n", ""),
    ("F1-B", "the exact-str check is dropped", "type(value) is str", "True"),
    (
        "F1-B",
        "an ambiguous pair of headers picks a winner",
        "if len(values) != 1:",
        "if len(values) < 1:",
    ),
    ("F1-G", "the cache field is dropped", '            "cache": {"no-cache": True},\n', ""),
    (
        "F1-F",
        "the fingerprint is not carried on the 200 path",
        "make, deployment_fingerprint=_deployment_fingerprint_from_headers(response.headers)",
        "make, deployment_fingerprint=None",
    ),
)


def test_zz_self_attack_each_fault_turns_this_file_red() -> None:
    if _PORT == 0 or NO_FAULTS:
        return
    src = MODULE_PATH.read_text()
    for criterion, label, needle, replacement in FAULTS:
        check("F1-S", f"{label}: the anchor is present verbatim", src.count(needle) >= 1)
        if needle not in src:
            continue
        mutated = src.replace(needle, replacement, 1)
        check(
            "F1-S",
            f"{label}: this file goes RED naming {criterion}",
            _red_under_fault(mutated, f"[{criterion}]"),
        )


def main() -> None:
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - a crash is a failed test, reported by name
                    FAILURES.append(f"FAIL [FTF-0616-F1] {name} raised {type(exc).__name__}: {exc}")
    finally:
        pass

    if FAILURES:
        print(f"FTF-0616-F1 judge fingerprint tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0616-F1 judge fingerprint tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
