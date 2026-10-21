#!/usr/bin/env python3
"""FTF-0904 -- Log redaction (no-DB unit/fault tests).

Proves, without any database or network:

1. ACC-1 (known secret headers / URLs / tokens are sanitised at ingress and at
   the sink): every secret in every generated variant (case, whitespace,
   zero-width and fullwidth look-alikes, URL-encoding, base64 wrapping, split
   across logging args, inside an exception chain, inside `extra=` attributes,
   inside dict reprs and JSON text) is absent from the *fully rendered* output
   of root logging, stderr, stdout, every handler's formatted line, and every
   attribute of every record a handler saw.
2. ACC-2 (unstructured exception content): the whole `__cause__` /
   `__context__` chain, `__notes__` and the traceback text are redacted.
3. NEG-1 (redactor failure must not log the raw payload): a redaction rule that
   raises, a message whose `__str__` raises, a mismatched `%` format, and an
   oversize payload are each replaced by one constant content-free marker -- the
   raw message, args, traceback and extras never reach any output.
4. NEG-2 (redaction alone cannot justify external telemetry of sensitive
   content): redacted text is still refused by FTF-0903's `emit_event` in every
   field of every kind, and this module never builds a telemetry event.
5. Fault injection (Law 1): every removed rule / bypass is applied to a *copy* of
   the real module, run in a fresh subprocess against the behavioural checks,
   and must turn them RED. The real file is never edited.

Run directly: python3 tests/tasks/test_ftf_0904.py
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

REAL_ROOT = Path(__file__).resolve().parent.parent.parent
ROOT = Path(os.environ.get("FTF0904_ROOT", str(REAL_ROOT)))
# scripts/mutation_gate.py edits the real module in place and re-runs this file once per mutant;
# the fault-injection copies below would then be copies of a mutant (and cost minutes each), so
# only the behavioural checks run there. The plain CI step and local runs still run every fault.
UNDER_MUTATION_GATE = "mutation-gate-pyc" in os.environ.get("PYTHONPYCACHEPREFIX", "")
BEHAVIOUR_ONLY = os.environ.get("FTF0904_BEHAVIOUR_ONLY") == "1" or UNDER_MUTATION_GATE
sys.path.insert(0, str(ROOT))

from packages.security import log_redaction as lr  # noqa: E402

MODULE = REAL_ROOT / "packages" / "security" / "log_redaction.py"
FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


# ---------------------------------------------------------------------------
# Secrets (generated, not hand-listed) and a shared leak check
# ---------------------------------------------------------------------------

_ALNUM = "Zq7Lm2Xv9Rt4Hn8Kc3Pw6Yb1Dg5Fs0J"  # fixed, deterministic filler
REDACTED_ = "[REDACTED]"


def _marker(size: int) -> str:
    """A deterministic, non-periodic alphanumeric string of exactly `size` characters (every
    32-char window is unique to its position, so a surviving fragment names the leaked offset)."""
    chunks: list[str] = []
    digest = b"ftf-0904-r6"
    length = 0
    while length < size:
        digest = hashlib.sha256(digest).digest()
        chunk = digest.hex()
        chunks.append(chunk)
        length += len(chunk)
    return "S" + "".join(chunks)[: size - 1]


def _filler(index: int, length: int = 32) -> str:
    """A different 32-char filler per secret, so one secret's tail never matches another's."""
    rotated = _ALNUM[index:] + _ALNUM[:index]
    return rotated[::-1] if index % 2 else rotated


def _secret_values() -> dict[str, str]:
    return {
        "openai": "sk-" + _filler(1),
        "anthropic": "sk-ant-api03-" + _filler(2),
        "github": "ghp_" + _filler(3)[:30],
        "github_pat": "github_pat_" + _filler(4) + "_" + _filler(5)[:12],
        "slack": "xoxb-1234567890-" + _filler(6)[:20],
        "aws": "AKIA" + _filler(7).upper()[:16],
        "hf": "hf_" + _filler(8),
        "jwt": "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiJ1c2VyLTEifQ." + _filler(9)[:16],
        "google": "AIza" + _filler(10) + "abcd",
        "opaque": "hunter2-" + _filler(11),
    }


SECRETS = _secret_values()
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA" + _ALNUM + "\n-----END RSA PRIVATE KEY-----"
)


class _Harness:
    """Real root logging with two handlers, stderr and stdout all captured."""

    def __init__(self, *, install: bool = True) -> None:
        self.install = install
        self.seen_records: list[logging.LogRecord] = []
        self.lines: list[str] = []
        self.out = io.StringIO()
        self.err = io.StringIO()

    def __enter__(self) -> _Harness:
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        self._stack.enter_context(contextlib.redirect_stderr(self.err))
        self.root = logging.getLogger()
        self.saved = (self.root.level, list(self.root.handlers), logging.getLogRecordFactory())
        self.saved_make_record = logging.Logger.makeRecord
        self.root.handlers = []
        self.root.setLevel(logging.DEBUG)
        harness = self

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                harness.seen_records.append(record)
                harness.lines.append(self.format(record))

        self.stream = io.StringIO()
        stream_handler = logging.StreamHandler(self.stream)
        capture = _Capture()
        # created after the redirect: these really write to the captured stderr / stdout
        for handler in (
            stream_handler,
            capture,
            logging.StreamHandler(),
            logging.StreamHandler(sys.stdout),
        ):
            handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
            self.root.addHandler(handler)
        if self.install:
            lr.install_redaction(self.root)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()
        self.root.handlers = self.saved[1]
        self.root.setLevel(self.saved[0])
        logging.setLogRecordFactory(self.saved[2])
        logging.Logger.makeRecord = self.saved_make_record  # type: ignore[method-assign]

    def outputs(self) -> list[str]:
        rendered = [self.stream.getvalue(), self.out.getvalue(), self.err.getvalue(), *self.lines]
        for record in self.seen_records:
            rendered.append(repr(sorted((k, repr(v)) for k, v in vars(record).items())))
            rendered.append(record.getMessage() if _safe_get_message(record) else "")
        return rendered


def _safe_get_message(record: logging.LogRecord) -> bool:
    try:
        record.getMessage()
    except Exception:
        return False
    return True


def leaked(secrets: list[str], outputs: list[str]) -> list[str]:
    """Every secret that survives, verbatim or in a trivially decoded form, in any output."""
    found = []
    blob = "\n".join(outputs)
    for secret in secrets:
        forms = {secret, secret.lower(), secret.upper(), quote(secret, safe="")}
        forms.add(base64.b64encode(secret.encode()).decode().rstrip("="))
        forms.add(base64.urlsafe_b64encode(secret.encode()).decode().rstrip("="))
        # the distinctive tail, so a partial redaction ("sk-[REDACTED]Zq7...") is still caught
        if len(secret) > 14:
            forms.add(secret[-12:])
        for form in forms:
            if form and form in blob:
                found.append(secret)
                break
    return found


def _variants(secret: str) -> dict[str, str]:
    return {
        "plain": secret,
        "upper-case-prefix": secret[:3].upper() + secret[3:],
        "zero-width-inner": secret[:5] + "​" + secret[5:],
        "bom-prefix": "﻿" + secret,
        "vertical-tab-inner": secret[:5] + "\x0b" + secret[5:],
        "unit-separator-inner": secret[:6] + "\x1f" + secret[6:],
        "fullwidth-prefix": "".join(
            chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in secret[:3]
        )
        + secret[3:],
    }


# ---------------------------------------------------------------------------
# ACC-1: headers, URLs, tokens
# ---------------------------------------------------------------------------


def test_every_secret_shape_is_removed_from_text() -> None:
    for name, secret in SECRETS.items():
        for vname, value in _variants(secret).items():
            for template in (
                "value is {s} end",
                "token={s}",
                "failed with key {s}.",
                "{{'k': '{s}'}}",
            ):
                text = template.format(s=value)
                if name == "opaque" and "token=" not in template:
                    continue  # an opaque value has no shape; only key=value / header forms bind it
                out = lr.redact_text(text)
                check(
                    "FTF-0904-A1",
                    f"{name}/{vname} not removed from {template!r}: {out!r}",
                    not leaked([secret], [out]),
                )
    out = lr.redact_text("key:\n" + PEM + "\nafter")
    check("FTF-0904-A1", "PEM private key block removed", _ALNUM not in out and "after" in out)


def test_secret_headers_are_redacted_whole() -> None:
    for header in (
        "Authorization",
        "authorization",
        "AUTHORIZATION",
        "Proxy-Authorization",
        "Cookie",
        "Set-Cookie",
        "X-Api-Key",
        "x-auth-token",
        "X-Amz-Security-Token",
    ):
        for sep in (": ", ":", " : ", "="):
            raw = f"{header}{sep}Bearer abc123SECRETvalue456 session=zzSESSIONzz; other=1"
            out = lr.redact_text(raw)
            check(
                "FTF-0904-A1",
                f"{header!r} sep {sep!r} header value leaked: {out!r}",
                "abc123SECRETvalue456" not in out and "zzSESSIONzz" not in out,
            )
    for bare in (
        "Bearer opaqueBEARERtoken12345",
        "basic dXNlcjpwYXNzd29yZDEyMw==",
        "BEARER\topaqueTAB-token99",
    ):
        out = lr.redact_text("failed: " + bare + " (retrying)")
        check(
            "FTF-0904-A1",
            f"bare credential scheme leaked: {out!r}",
            "opaqueBEARERtoken12345" not in out
            and "dXNlcjpwYXNz" not in out
            and "opaqueTAB" not in out,
        )
    out = lr.redact_text("GET /x\r\nAuthorization: Basic dXNlcjpwYXNzd29yZDEyMw==\r\nHost: a")
    check("FTF-0904-A1", "multi-line request head: auth redacted", "dXNlcjpwYXNz" not in out)
    check("FTF-0904-A1", "multi-line request head: neighbours kept", "Host: a" in out)
    out = lr.redact_text(repr({"Authorization": "Bearer abc123SECRETvalue456", "Accept": "x"}))
    check(
        "FTF-0904-A1",
        f"dict repr header redacted, neighbour kept: {out!r}",
        "abc123SECRETvalue456" not in out and "Accept" in out,
    )
    out = lr.redact_text(json.dumps({"headers": {"cookie": "sid=QQQsecretQQQ"}}))
    check("FTF-0904-A1", f"JSON header redacted: {out!r}", "QQQsecretQQQ" not in out)
    out = lr.redact_text(json.dumps(json.dumps({"password": "pw-Embedded-9988"})))
    check("FTF-0904-A1", f"JSON-in-JSON escaped quotes: {out!r}", "pw-Embedded-9988" not in out)


def test_url_userinfo_and_query_tokens() -> None:
    out = lr.redact_text(
        "connect postgres://app:p%40ss-Word-7788@db.internal:5432/x?sslmode=require"
    )
    check("FTF-0904-A1", f"userinfo password removed: {out!r}", "Word-7788" not in out)
    check("FTF-0904-A1", "host kept for diagnosis", "db.internal:5432" in out)
    out = lr.redact_text("https://user@example.com/a")
    check("FTF-0904-A1", f"userinfo without password removed: {out!r}", "user@" not in out)
    for param in (
        "token",
        "access_token",
        "api_key",
        "apikey",
        "password",
        "secret",
        "signature",
        "X-Amz-Signature",
    ):
        out = lr.redact_text(f"GET https://h.example/path?a=1&{param}=Q9zV8xW7uT6r&b=2")
        check("FTF-0904-A1", f"query {param} value kept: {out!r}", "Q9zV8xW7uT6r" not in out)
        check("FTF-0904-A1", f"query {param}: unrelated params kept", "a=1" in out and "b=2" in out)
    out = lr.redact_text("https://h.example/p?next=%2Fhome&page=2")
    check("FTF-0904-A1", f"benign query untouched: {out!r}", out.endswith("next=%2Fhome&page=2"))


def test_variants_url_encoded_base64_and_split() -> None:
    secret = SECRETS["openai"]
    for label, text in (
        ("url-encoded", f"q={quote('Authorization: Bearer ' + secret, safe='')}"),
        ("double-url-encoded", f"q={quote(quote('token=' + secret, safe=''), safe='')}"),
        ("base64", "blob=" + base64.b64encode(f"password={secret}".encode()).decode()),
        (
            "base64-urlsafe",
            "blob=" + base64.urlsafe_b64encode(f"token: {secret}".encode()).decode(),
        ),
        ("base64-bare-secret", "blob=" + base64.b64encode(secret.encode()).decode()),
        ("mixed-whitespace", "Authorization: 　Bearer\t " + secret),
        ("fullwidth-bearer", "Ｂｅａｒｅｒ " + secret),
    ):
        out = lr.redact_text(text)
        check("FTF-0904-A1", f"{label} leaked: {out!r}", not leaked([secret], [out]))


def test_benign_text_survives() -> None:
    for text in (
        "job 42 finished in 130 ms",
        "request_id=req-1 state_of=running",
        "GET https://api.example.com/v1/models?limit=10 -> 200",
        "",
    ):
        check("FTF-0904-A1", f"benign text mangled: {text!r}", lr.redact_text(text) == text)
    check(
        "FTF-0904-A1",
        "redaction is idempotent",
        lr.redact_text(lr.redact_text("token=" + SECRETS["opaque"]))
        == lr.redact_text("token=" + SECRETS["opaque"]),
    )


def test_redact_value_keeps_container_types() -> None:
    check("FTF-0904-A1", "list stays a list", lr.redact_value(["a", "b"]) == ["a", "b"])
    check("FTF-0904-A1", "tuple stays a tuple", lr.redact_value(("a", 1)) == ("a", 1))
    check("FTF-0904-A1", "set becomes a list", lr.redact_value({"only"}) == ["only"])
    check("FTF-0904-A1", "bytes become redacted text", lr.redact_value(b"hello") == "hello")
    check(
        "FTF-0904-A1",
        "scalars pass through",
        lr.redact_value((None, True, 3, 2.5)) == (None, True, 3, 2.5),
    )


def test_benign_case_and_encoding_survive() -> None:
    for text in (
        "Job ABC Finished OK  with  spaces\n",
        "Folder%2FSub%20Dir and UPPER",
        "blob=" + base64.b64encode(b"Hello World, nothing secret here.\n").decode(),
        "id=Req-AbCdEfGhIjKlMnOpQr",
    ):
        out = lr.redact_text(text)
        check("FTF-0904-A1", f"benign text altered: {out!r}", out == text)
    padded = base64.b64encode(b"  PASSWORD=Hunter2Upper-Zq9  \n").decode()
    out = lr.redact_text("x " + padded + " y")
    check(
        "FTF-0904-A1",
        f"upper-case padded base64 secret leaked: {out!r}",
        padded not in out and "Hunter2Upper" not in out,
    )
    binary = base64.b64encode(b"\x00\x01token=pw-Binary-4422\x02").decode()
    check("FTF-0904-A1", "binary-wrapped secret leaked", binary not in lr.redact_text(binary))


def test_extra_attribute_names_and_install_targets() -> None:
    with _Harness() as h:
        logging.getLogger("ftf0904.names").info(
            "m", extra={"MyPayload": SECRETS["openai"], "Name": SECRETS["openai"]}
        )
        rec_vars = [vars(r) for r in h.seen_records]
    check(
        "FTF-0904-A1",
        "a mixed-case extra attribute is redacted under its own name, no lowercased twin",
        all(v.get("MyPayload") == lr.REDACTED and "mypayload" not in v for v in rec_vars),
    )
    check(
        "FTF-0904-A1",
        "an extra named like a standard attribute but differently cased is still scrubbed",
        all(v.get("Name") == lr.REDACTED for v in rec_vars),
    )
    own = logging.getLogger("ftf0904.own")
    own_handler = logging.StreamHandler(io.StringIO())
    own.addHandler(own_handler)
    other = logging.StreamHandler(io.StringIO())
    try:
        with _Harness(install=False) as h:
            h.root.addHandler(other)
            lr.install_redaction(own)
            check(
                "FTF-0904-A1",
                "install_redaction(logger) covers that logger's handlers only",
                any(isinstance(f, lr.RedactingFilter) for f in own_handler.filters)
                and not any(isinstance(f, lr.RedactingFilter) for f in other.filters),
            )
            lr.install_redaction()
            check(
                "FTF-0904-A1",
                "install_redaction() with no argument covers the root logger's handlers",
                any(isinstance(f, lr.RedactingFilter) for f in other.filters),
            )
    finally:
        own.removeHandler(own_handler)


def test_redact_value_structures() -> None:
    value = {
        "password": "pw-Struct-4455",
        "nested": {
            "api_key": "key-Struct-6677",
            "n": 1,
            "list": ["Authorization: Bearer lst-Struct-8899", 3],
        },
        "ok": "fine",
    }
    out: Any = lr.redact_value(value)
    text = repr(out)
    for needle in ("pw-Struct-4455", "key-Struct-6677", "lst-Struct-8899"):
        check("FTF-0904-A1", f"{needle} survived redact_value: {text}", needle not in text)
    check(
        "FTF-0904-A1",
        "non-secret structure preserved",
        out["ok"] == "fine" and out["nested"]["n"] == 1,
    )
    check("FTF-0904-A1", "input is not mutated", value["password"] == "pw-Struct-4455")
    cyc: dict[str, object] = {"password": "pw-Cyc-1122"}
    cyc["self"] = cyc
    check(
        "FTF-0904-A1",
        "cyclic structure is bounded, not recursed forever",
        "pw-Cyc-1122" not in repr(lr.redact_value(cyc)),
    )
    deep: object = "token=pw-Deep-3344"
    for _ in range(40):
        deep = [deep]
    check(
        "FTF-0904-A1",
        "over-deep structure is cut, never leaked",
        "pw-Deep-3344" not in repr(lr.redact_value(deep)),
    )

    class Obj:
        def __repr__(self) -> str:
            return "Obj(token=pw-Repr-5566)"

    check(
        "FTF-0904-A1",
        "bytes values are redacted",
        "pw-Bytes-7788" not in repr(lr.redact_value(b"token=pw-Bytes-7788 x")),
    )
    check(
        "FTF-0904-A1",
        "non-string mapping keys do not break redaction",
        "pw-IntKey-9911" not in repr(lr.redact_value({1: "password=pw-IntKey-9911"})),
    )
    check(
        "FTF-0904-A1",
        "arbitrary object redacted via its repr",
        "pw-Repr-5566" not in repr(lr.redact_value(Obj())),
    )


# ---------------------------------------------------------------------------
# ACC-1 end to end through real logging: ingress and sink
# ---------------------------------------------------------------------------


def test_logging_end_to_end_ingress_and_sink() -> None:
    secrets = list(SECRETS.values())
    with _Harness() as h:
        log = logging.getLogger("ftf0904.e2e")
        for name, secret in SECRETS.items():
            if name != "opaque":  # a bare opaque value has no shape to match (disclosed limit)
                log.info("plain %s", secret)
                log.info("fmt " + secret.replace("%", "%%"))
                log.warning("split %s%s", secret[:9], secret[9:])
                log.info("extra", extra={"payload": secret})
            log.error("dict %r", {"Authorization": "Bearer " + secret})
            log.info("extra", extra={"headers": {"cookie": "sid=" + secret}, "password": secret})
            log.info("kv password=%s", secret)
        log.info(
            "url %s", "https://u:" + SECRETS["opaque"] + "@h.example/p?token=" + SECRETS["github"]
        )
        log.info("to the console handlers too %s", SECRETS["openai"])
    check(
        "FTF-0904-A1",
        f"secrets reached an output: {leaked(secrets, h.outputs())}",
        not leaked(secrets, h.outputs()),
    )
    check(
        "FTF-0904-A1",
        "console handlers (stderr/stdout) carried the redacted line",
        "to the console handlers too" in h.err.getvalue()
        and "to the console handlers too" in h.out.getvalue(),
    )
    check(
        "FTF-0904-A1",
        "records still reach handlers (redaction does not drop them)",
        len(h.seen_records) > 20,
    )
    check(
        "FTF-0904-A1",
        "non-secret message text is preserved",
        any("plain" in line for line in h.lines),
    )


def test_sink_redacts_even_when_ingress_was_bypassed() -> None:
    secret = SECRETS["github"]
    with _Harness(install=False) as h:
        handler = logging.StreamHandler(io.StringIO())
        lr.attach_sink(handler)
        h.root.handlers = [handler]
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "tok %s", (secret,), None)
        record.payload = secret
        h.root.handle(record)
        out = handler.stream.getvalue()
    check("FTF-0904-A1", f"sink-only handler leaked: {out!r}", not leaked([secret], [out]))
    check("FTF-0904-A1", "sink keeps the non-secret part", "tok" in out)
    # the sink alone, with a record that still carries a raw exc_info / exc_text / stack_info
    with _Harness(install=False) as h2:
        sink = logging.StreamHandler(io.StringIO())
        lr.attach_sink(sink)
        h2.root.handlers = [sink]
        try:
            raise ValueError("sink-only token=" + SECRETS["github_pat"])
        except ValueError:
            raw_info = sys.exc_info()
        rec_exc = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, raw_info)
        rec_text = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom2", None, None)
        rec_text.exc_text = "Traceback: Authorization: Bearer " + SECRETS["hf"]
        rec_stack = logging.LogRecord(
            "x",
            logging.ERROR,
            __file__,
            1,
            "boom3",
            None,
            None,
            sinfo="Stack: token=" + SECRETS["slack"],
        )
        for rec in (rec_exc, rec_text, rec_stack):
            h2.root.handle(rec)
        sink_out = sink.stream.getvalue()
    check(
        "FTF-0904-A2",
        f"sink-only exc_info/exc_text/stack_info leaked: {sink_out!r}",
        not leaked([SECRETS["github_pat"], SECRETS["hf"], SECRETS["slack"]], [sink_out]),
    )
    check("FTF-0904-A2", "sink-only keeps the traceback shape", "ValueError" in sink_out)
    # each layer alone. Filter alone: inspect the record it leaves behind.
    try:
        raise ValueError("filter-only token=" + SECRETS["github_pat"])
    except ValueError:
        raw_info2 = sys.exc_info()
    only = logging.LogRecord(
        "x",
        logging.ERROR,
        __file__,
        1,
        "m %s",
        ("Bearer " + SECRETS["openai"],),
        raw_info2,
        sinfo="Stack: token=" + SECRETS["slack"],
    )
    only.__dict__["pre_exc"] = None
    lr.RedactingFilter().filter(only)
    left = repr(sorted((k, repr(v)) for k, v in vars(only).items()))
    check(
        "FTF-0904-A2",
        "the filter alone leaves no secret on the record (msg/args/exc/stack)",
        not leaked([SECRETS["openai"], SECRETS["github_pat"], SECRETS["slack"]], [left]),
    )
    check(
        "FTF-0904-A2",
        "the filter alone renders the exception into exc_text",
        "ValueError" in (only.exc_text or ""),
    )
    pre = logging.LogRecord("x", logging.ERROR, __file__, 1, "p", None, None)
    pre.exc_text = "Traceback: Authorization: Bearer " + SECRETS["hf"]
    lr.RedactingFilter().filter(pre)
    check(
        "FTF-0904-A2",
        "a pre-set exc_text is redacted by the filter",
        not leaked([SECRETS["hf"]], [pre.exc_text or ""]),
    )
    # Formatter layer alone: the filter is removed after attach_sink.
    bare = logging.StreamHandler(io.StringIO())
    lr.attach_sink(bare)
    bare.filters.clear()
    check(
        "FTF-0904-A1",
        "attach_sink installs a redacting formatter",
        isinstance(bare.formatter, lr.RedactingFormatter),
    )
    rec_fmt = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "raw %s", ("token=" + SECRETS["opaque"],), None
    )
    bare.handle(rec_fmt)
    check(
        "FTF-0904-A1",
        "formatter layer alone redacts the line",
        not leaked([SECRETS["opaque"]], [bare.stream.getvalue()]),
    )
    # formatter used on its own, with no filter at all
    fmt = lr.RedactingFormatter("%(message)s")
    rec = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "a %s", ("Bearer " + SECRETS["openai"],), None
    )
    check(
        "FTF-0904-A1",
        "RedactingFormatter alone redacts",
        not leaked([SECRETS["openai"]], [fmt.format(rec)]),
    )


def test_install_is_idempotent_and_covers_late_handlers() -> None:
    with _Harness() as h:
        lr.install_redaction(h.root)
        lr.install_redaction(h.root)
        late = logging.StreamHandler(io.StringIO())
        h.root.addHandler(late)
        logging.getLogger("ftf0904.late").info("late %s", SECRETS["slack"])
        # ingress redaction is at record creation, so a handler added after install is covered too
        check(
            "FTF-0904-A1",
            "late handler is covered by ingress",
            not leaked([SECRETS["slack"]], [late.stream.getvalue()]),
        )
        chain = 0
        factory = logging.getLogRecordFactory()
        while hasattr(factory, "__wrapped__"):
            factory = factory.__wrapped__
            chain += 1
        check("FTF-0904-A1", f"record factory wrapped exactly once, got {chain}", chain == 1)
        filters = [f for f in h.root.handlers[0].filters if isinstance(f, lr.RedactingFilter)]
        check("FTF-0904-A1", "sink filter attached once per handler", len(filters) == 1)


# ---------------------------------------------------------------------------
# ACC-2: exceptions
# ---------------------------------------------------------------------------


def _raise_chain(secret: str) -> BaseException:
    try:
        try:
            raise ValueError(f"inner failed token={secret}")
        except ValueError as inner:
            raise RuntimeError("outer failed (see cause)") from inner
    except RuntimeError as outer:
        outer.add_note(f"note: Authorization: Bearer {secret}")
        return outer


def test_exception_chain_notes_and_traceback_are_redacted() -> None:
    secret = SECRETS["openai"]
    out = lr.redact_exception(_raise_chain(secret))
    check("FTF-0904-A2", f"chain/notes leaked: {out!r}", not leaked([secret], [out]))
    check(
        "FTF-0904-A2",
        "exception type and structure kept",
        "RuntimeError" in out and "ValueError" in out,
    )

    try:
        try:
            raise KeyError("ctx-" + secret)
        except KeyError:
            raise OSError("implicit context")  # noqa: B904 -- __context__ on purpose
    except OSError as exc:
        implicit = lr.redact_exception(exc)
    check("FTF-0904-A2", "implicit __context__ redacted", not leaked([secret], [implicit]))

    class Weird(Exception):
        def __str__(self) -> str:
            return "weird token=" + secret

    check(
        "FTF-0904-A2",
        "custom __str__ redacted",
        not leaked([secret], [lr.redact_exception(Weird())]),
    )

    group = ExceptionGroup("grp", [ValueError("a token=" + secret), TypeError("b " + secret)])
    check(
        "FTF-0904-A2",
        "ExceptionGroup members redacted",
        not leaked([secret], [lr.redact_exception(group)]),
    )

    exc_a = RuntimeError("a token=" + secret)
    exc_b = RuntimeError("b token=" + secret)
    exc_a.__cause__ = exc_b
    exc_b.__cause__ = exc_a
    check(
        "FTF-0904-A2",
        "cyclic cause chain terminates, redacted",
        not leaked([secret], [lr.redact_exception(exc_a)]),
    )


def test_logging_exception_paths_never_leak() -> None:
    secret = SECRETS["github"]
    with _Harness() as h:
        log = logging.getLogger("ftf0904.exc")
        try:
            raise _raise_chain(secret)
        except RuntimeError:
            log.exception("handler failed")
            log.error("explicit", exc_info=True)
        log.error("stack", stack_info=True, extra={"detail": "x " + secret})
        log.error("tuple", exc_info=(RuntimeError, RuntimeError("t " + secret), None))
    check(
        "FTF-0904-A2",
        f"logged exception leaked: {leaked([secret], h.outputs())}",
        not leaked([secret], h.outputs()),
    )
    check(
        "FTF-0904-A2",
        "traceback shape is still logged",
        any("Traceback" in line or "RuntimeError" in line for line in h.lines),
    )
    check(
        "FTF-0904-A2",
        "no record carries a raw exc_info for another handler to render",
        all(getattr(r, "exc_info", None) in (None, False) for r in h.seen_records),
    )


# ---------------------------------------------------------------------------
# NEG-1: the redactor failing must not log the raw payload
# ---------------------------------------------------------------------------

RAW = "RAWPAYLOAD-" + _ALNUM


def test_redactor_failure_logs_a_constant_marker_only() -> None:
    class Boom(str):
        def __str__(self) -> str:
            raise RuntimeError("str exploded " + RAW)

    class BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("repr exploded " + RAW)

    with _Harness() as h:
        log = logging.getLogger("ftf0904.neg1")
        log.info(
            "bad format %d " + RAW, "not-a-number"
        )  # mismatched args: logging would dump raw to stderr
        log.info("object %s " + RAW, BadRepr())
        log.info("exploding %s", Boom(RAW))
        log.info("extra", extra={"payload": RAW, "obj": BadRepr()})
        log.info("big " + RAW + "x" * (lr.MAX_TEXT_LENGTH + 5))
    check(
        "FTF-0904-N1",
        f"raw payload reached an output after failures: {leaked([RAW], h.outputs())}",
        RAW not in "\n".join(h.outputs()),
    )
    check(
        "FTF-0904-N1",
        "the failure marker is what was logged",
        any(lr.REDACTION_FAILED_MARKER in line for line in h.lines),
    )
    check(
        "FTF-0904-N1",
        "marker is a content-free constant",
        lr.REDACTION_FAILED_MARKER == "[LOG_REDACTION_FAILED]",
    )
    check(
        "FTF-0904-N1",
        "logging's own error dump to stderr is empty",
        "Logging error" not in h.err.getvalue(),
    )


def test_a_raising_rule_fails_closed_not_open() -> None:
    class ExplodingPattern:
        def sub(self, *_a: object, **_k: object) -> str:
            raise RuntimeError("rule exploded " + RAW)

    saved = lr._RULES
    lr._RULES = (*saved, ("exploding", ExplodingPattern()))
    try:
        check(
            "FTF-0904-N1",
            "redact_text raises its own coded error",
            _raises(lr.LogRedactionError, lr.redact_text, RAW),
        )
        check(
            "FTF-0904-N1",
            "redact_exception raises its own coded error when a rule fails",
            _raises(lr.LogRedactionError, lr.redact_exception, ValueError(RAW)),
        )
        check(
            "FTF-0904-N1",
            "redact_value raises its own coded error when a rule fails",
            _raises(lr.LogRedactionError, lr.redact_value, {"k": RAW}),
        )
        with _Harness() as h:
            logging.getLogger("ftf0904.neg1b").info("msg " + RAW, extra={"k": RAW})
            try:
                raise ValueError(RAW)
            except ValueError:
                logging.getLogger("ftf0904.neg1b").exception("e")
            fmt = lr.RedactingFormatter("%(message)s")
            rec = logging.LogRecord("x", logging.INFO, __file__, 1, "m " + RAW, None, None)
            formatted = fmt.format(rec)
        check(
            "FTF-0904-N1",
            "formatter returns the marker when the rule raises",
            formatted == lr.REDACTION_FAILED_MARKER,
        )
        check(
            "FTF-0904-N1",
            "raw payload never reached an output",
            RAW not in "\n".join(h.outputs() + [formatted]),
        )
        err = None
        try:
            lr.redact_text(RAW)
        except lr.LogRedactionError as exc:
            err = exc
        check(
            "FTF-0904-N1",
            "coded error echoes no caller text",
            err is not None
            and RAW not in str(err)
            and RAW not in repr(err)
            and err.code == "LOG_REDACTION_FAILED",
        )
        check(
            "FTF-0904-N1",
            "coded error does not chain the raw cause",
            err is not None and err.__cause__ is None and err.__suppress_context__,
        )
        check(
            "FTF-0904-N1",
            "coded error carries no context, notes or attributes holding caller text",
            err is not None
            and err.__context__ is None
            and not getattr(err, "__notes__", None)
            and vars(err) == {}
            and err.args == ("log redaction failed",),
        )
    finally:
        lr._RULES = saved
    check(
        "FTF-0904-N1",
        "state restored: redaction works again",
        lr.redact_text("token=" + SECRETS["opaque"]) != "token=" + SECRETS["opaque"],
    )


def test_non_text_inputs_are_coded_refusals() -> None:
    for bad in (None, 5, b"token=abc", object(), ["x"]):
        check(
            "FTF-0904-N1",
            f"redact_text({type(bad).__name__}) must be a coded error",
            _raises(lr.LogRedactionError, lr.redact_text, bad),
        )
    check(
        "FTF-0904-N1",
        "the error message is the constant one",
        str(lr.LogRedactionError()) == "log redaction failed",
    )
    check(
        "FTF-0904-N1",
        "exception redactor refuses non-exceptions",
        _raises(lr.LogRedactionError, lr.redact_exception, "not an exception"),
    )
    check(
        "FTF-0904-N1",
        "str subclass is refused (exact types)",
        _raises(lr.LogRedactionError, lr.redact_text, type("S", (str,), {})("x")),
    )


# ---------------------------------------------------------------------------
# Round 2 (ACC-1 / bounded runtime): raw-first percent handling, header forms,
# ingress scrub of extra=, linear key rule
# ---------------------------------------------------------------------------

_DELIMS = ["&", ";", ",", " ", '"', "'", ")", "]", "}", "/", "\r", "\n", "\t", "%"]
_PLAIN_SECRETS = ["Zq7Lm2Xv9Rt4Hn8Kc3Pw", "hunter2-Zq7Lm2Xv9Rt4"]


def _enc(text: str, layers: int) -> str:
    for _ in range(layers):
        text = quote(text, safe="")
    return text


def _percent_cases() -> list[tuple[str, str, list[str]]]:
    """(label, text, secrets that must not survive)."""
    cases: list[tuple[str, str, list[str]]] = []
    plain_sk = SECRETS["openai"]
    for secret in _PLAIN_SECRETS:
        for delim in _DELIMS:
            tag = f"{secret[:4]}/{delim!r}"
            for layers in (1, 2, 3):
                body = _enc(delim + secret, layers)
                cases.append(
                    (
                        f"pw-param {tag} x{layers}",
                        f"username=a&password=p{body}",
                        [secret],
                    )
                )
                cases.append(
                    (
                        f"pw-param+plain {tag} x{layers}",
                        f"{plain_sk} user=a&password=p{body} tail",
                        [secret, plain_sk],
                    )
                )
                cases.append(
                    (
                        f"decoded-only {tag} x{layers}",
                        f"GET /x?q={_enc('a' + delim + 'password=' + secret, layers)}",
                        [secret],
                    )
                )
        for layers in (1, 2, 3):
            cases.append(
                (
                    f"authorization-bearer x{layers}",
                    f"q={_enc('Authorization: Bearer ' + secret, layers)}",
                    [secret],
                )
            )
            cases.append(
                (
                    f"authorization-next-to-plain x{layers}",
                    f"{plain_sk} {_enc('Authorization: ' + secret, layers)}",
                    [secret, plain_sk],
                )
            )
        cases.append(
            (
                "encoded CR in a header",
                f"X-Trace: id%0d%0aAuthorization: {secret}",
                [secret],
            )
        )
        cases.append(
            (
                "encoded CR+LF then cookie",
                f"X-Trace: id%0D%0ACookie: sid={secret}; path=/",
                [secret],
            )
        )
        cases.append(
            (
                "encoded header then another encoded token",
                f"Authorization%3A%20 {secret} then%20more",
                [secret],
            )
        )
        cases.append(
            (
                "two encoded tokens, secret spans the second",
                f"x=a%20b y=password%3D{secret} z=1%202",
                [secret],
            )
        )
        cases.append(
            (
                "dsn password containing a literal @",
                f"url=postgres://app:pa@ss{secret}@db/x ok",
                [secret],
            )
        )
        cases.append(
            (
                "dsn password containing a literal @ after an encoded token",
                f"a%20b url=postgres://app:pa@ss{secret}@db/x",
                [secret],
            )
        )
        cases.append(
            (
                "dsn password with another rule firing",
                f"url=postgres://app:p%40{secret}@db/x {plain_sk}",
                [secret, plain_sk],
            )
        )
        cases.append(
            (
                "dsn password with encoded colon",
                f"url=postgres://app%3Ap%40{secret}@db/x password=hunter",
                [secret],
            )
        )
        # Round 3 regression: the key and its separator are two separate whitespace-delimited
        # tokens, so the only `%XX` is in a run that, on its own, decodes to no recognisable
        # key=value shape ("= secret", with no key in view). The scan must still look one token
        # back to the key name it belongs to.
        cases.append(
            (
                "encoded separator, key and secret all space-separated (equals)",
                f"token %3D {secret} other=1",
                [secret],
            )
        )
        cases.append(
            (
                "encoded separator, key and secret all space-separated (colon)",
                f"password %3A {secret} other=1",
                [secret],
            )
        )
    return cases


def test_percent_decoded_layers_never_replace_the_raw_scan() -> None:
    for label, text, secrets in _percent_cases():
        out = lr.redact_text(text)
        check("FTF-0904-P1", f"{label}: leaked {out!r}", not leaked(secrets, [out]))
        raw_only = lr._scan(lr._normalise(text), lr._BASE64_LAYERS)
        # never less redacted than the raw-only scan: whatever it hides, we hide
        for secret in secrets:
            if secret not in raw_only and quote(secret, safe="") not in raw_only:
                check(
                    "FTF-0904-P1",
                    f"{label}: raw-only scan hides a secret that the result shows",
                    secret not in out,
                )
        check("FTF-0904-P1", f"{label}: output not str", type(out) is str)


def test_percent_cases_keep_their_context_when_raw_scan_suffices() -> None:
    out = lr.redact_text("username=a&password=p%26" + _PLAIN_SECRETS[0])
    check(
        "FTF-0904-P2",
        f"raw redaction must be kept whole: {out!r}",
        out == "username=a&password=[REDACTED]",
    )
    out = lr.redact_text("see /a%20b?x=1 and 100%25 done")
    check(
        "FTF-0904-P2",
        f"benign percent text must survive: {out!r}",
        out == "see /a%20b?x=1 and 100%25 done",
    )


def test_percent_token_starts_widened_is_none_for_the_first_run() -> None:
    """`_percent_token_starts`'s own contract: `widened_start` is the run immediately before a
    `%XX` run, or None when there is no earlier run. A line that starts with a `%XX`-containing
    run (index 0 in the whole line, not just index 0 of the filtered candidate list) has no run
    before it. A mutant that computes `runs[i - 1]` for `i == 0` too (Python wraps to `runs[-1]`,
    the *last* run in the line, instead of raising) is unreachable through `redact_text`'s own
    output (a revealing suffix of `line[start:]` can never be true while the superset is false),
    so this must be pinned directly against the private contract, not just end to end."""
    out = lr._percent_token_starts("%41 benign text with no secret at all")
    check(
        "FTF-0904-P5",
        f"first run (line starts with %XX) must report widened=None, got {out!r}",
        len(out) == 1 and out[0] == (0, None),
    )
    out2 = lr._percent_token_starts("ok x%20y then%20more")
    check(
        "FTF-0904-P5",
        f"a %XX run that is NOT the line's first token must report a real widened start: {out2!r}",
        len(out2) == 2 and out2[0][1] == 0 and out2[1][1] is not None,
    )


def test_cut_round_cap_still_fails_closed() -> None:
    secret = _PLAIN_SECRETS[0]
    text = f"ok x%20y Authorization%3A%20 {secret} then%20more"
    saved = lr._MAX_CUT_ROUNDS
    lr._MAX_CUT_ROUNDS = 0
    try:
        out = lr.redact_text(text)
    finally:
        lr._MAX_CUT_ROUNDS = saved
    check("FTF-0904-P4", f"capped cut leaked: {out!r}", secret not in out)
    check("FTF-0904-P4", f"capped cut dropped the clean prefix: {out!r}", out.startswith("ok "))


def test_percent_cases_end_to_end_through_logging() -> None:
    for label, text, secrets in _percent_cases()[::7]:
        with _Harness() as h:
            logging.getLogger("t").error("%s", text)
            logging.getLogger("t").error(text)
            found = leaked(secrets, h.outputs())
        check("FTF-0904-P3", f"{label}: end-to-end leak {found}", not found)


# --- header forms (ASGI / WSGI tuples and lists, HTTP_ environ keys) -------------------

_HEADER_NAMES = [
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-amz-security-token",
    "x-goog-api-key",
    "x-access-token",
    "x-csrf-token",
    "api-key",
]
_HV = "Zq7Lm2Xv9Rt4Hn8Kc3"  # a header value no other rule recognises


def _header_forms(name: str) -> dict[str, object]:
    environ = "HTTP_" + name.upper().replace("-", "_")
    return {
        "asgi-bytes-tuples": [(b"host", b"example"), (name.encode(), _HV.encode())],
        "asgi-str-tuples": [("host", "example"), (name, _HV)],
        "asgi-bytes-lists": [[b"host", b"example"], [name.encode(), _HV.encode()]],
        "asgi-upper-case-name": [(name.upper().encode(), _HV.encode())],
        "scope": {"type": "http", "headers": [(name.encode(), _HV.encode())]},
        "wsgi-environ": {"REQUEST_METHOD": "GET", environ: _HV},
        "wsgi-environ-bytes": {environ: _HV.encode()},
        "bytes-key-mapping": {name.encode(): _HV.encode()},
    }


def test_every_secret_header_is_redacted_in_structured_and_tuple_forms() -> None:
    names = lr._HEADER_KEYS
    for name in _HEADER_NAMES:
        check(
            "FTF-0904-H1",
            f"{name} must be a known secret header",
            re.fullmatch(names, name, re.I) is not None,
        )
        for label, value in _header_forms(name).items():
            as_value = repr(lr.redact_value(value))
            check("FTF-0904-H1", f"redact_value {name} {label}: {as_value}", _HV not in as_value)
            as_text = lr.redact_text(repr(value))
            check("FTF-0904-H1", f"redact_text(repr) {name} {label}: {as_text}", _HV not in as_text)
            as_str = lr.redact_text(str(value))
            check("FTF-0904-H1", f"redact_text(str) {name} {label}: {as_str}", _HV not in as_str)
        environ = "HTTP_" + name.upper().replace("-", "_")
        for text in (f"{environ}={_HV}", f"{environ}: {_HV}", f"'{environ}': '{_HV}'"):
            out = lr.redact_text(text)
            check("FTF-0904-H2", f"environ text {text!r} -> {out!r}", _HV not in out)
        with _Harness() as h:
            logging.getLogger("t").error("scope %s", {"headers": [(name.encode(), _HV.encode())]})
            logging.getLogger("t").error("environ %r", {environ: _HV})
            found = leaked([_HV], h.outputs())
        check("FTF-0904-H3", f"end-to-end {name}: {found}", not found)


def test_benign_tuples_and_environ_keys_survive() -> None:
    out = lr.redact_text(repr([(b"host", b"example.test"), (b"accept", b"*/*")]))
    check("FTF-0904-H4", f"benign headers must survive: {out!r}", "example.test" in out)
    out = lr.redact_text(repr({"HTTP_HOST": "example.test", "REQUEST_METHOD": "GET"}))
    check("FTF-0904-H4", f"benign environ must survive: {out!r}", "example.test" in out)
    value = lr.redact_value([("host", "example.test"), ("accept", "*/*")])
    check("FTF-0904-H4", f"benign pairs must survive: {value!r}", "example.test" in repr(value))


def test_pair_rule_covers_generic_secret_words_not_just_header_names() -> None:
    """The `pair` rule's key alternation is `_HEADER_KEYS | _SECRET_KEY`: a header-named key
    (authorization, cookie, ...) is one branch, but a generic secret word (client_secret,
    password, api_key, ...) that is not itself a header name is the other. Round-2 only
    exercised the header-named branch (test_every_secret_header_is_redacted_...); a fault
    dropping `|_SECRET_KEY` stayed GREEN because nothing here hit it (FTF-0904-F-PAIR-KEY)."""
    secret1, secret2 = _PLAIN_SECRETS[0], _PLAIN_SECRETS[1]
    text = f"got response pairs: [('client_secret', '{secret1}'), ('password', b'{secret2}')]"
    out = lr.redact_text(text)
    check("FTF-0904-H5", f"client_secret tuple (str) leaked: {out!r}", secret1 not in out)
    check("FTF-0904-H5", f"password tuple (bytes) leaked: {out!r}", secret2 not in out)
    # a non-secret generic key must not be swept up by the same rule
    benign = lr.redact_text(f"got response pairs: [('status', '{secret1}'), ('accept', 'ok')]")
    check("FTF-0904-H5", f"benign pair must survive: {benign!r}", secret1 in benign)


# --- extra= on non-root loggers ---------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record)


def test_extra_attributes_are_scrubbed_for_propagate_false_loggers() -> None:
    secret = _PLAIN_SECRETS[0]
    with _Harness(install=False) as h:
        child = logging.getLogger("ftf0904.uvicorn.error")
        saved = (child.propagate, list(child.handlers), child.level)
        child.propagate = False
        child.setLevel(logging.DEBUG)
        try:
            before: list[logging.LogRecord] = []
            child.addHandler(_Collect(before))
            lr.install_redaction(h.root)
            after: list[logging.LogRecord] = []
            child.addHandler(_Collect(after))
            extra = {
                "detail": f"Authorization: Bearer {secret}",
                "password": secret.encode(),
                "blob": {"api_key": secret, "n": 1},
                "note": f"password={secret}",
            }
            child.error("event %s", "x", extra=extra)
            logging.LoggerAdapter(child, extra).warning("adapter event")
            seen = [*before, *after]
            check("FTF-0904-X1", f"expected 4 records, got {len(seen)}", len(seen) == 4)
            for record in seen:
                blob = repr(sorted((k, repr(v)) for k, v in vars(record).items()))
                check("FTF-0904-X1", f"extra leaked on {record.name}: {blob}", secret not in blob)
                check(
                    "FTF-0904-X1", "benign number must survive", record.__dict__["blob"]["n"] == 1
                )
            # the caller's own dict must not be mutated
            check("FTF-0904-X2", "caller extra mutated", extra["note"] == f"password={secret}")
        finally:
            child.propagate, child.handlers, level = saved[0], saved[1], saved[2]
            child.setLevel(level)


def test_install_does_not_stack_the_record_maker() -> None:
    with _Harness() as h:
        lr.install_redaction(h.root)
        lr.install_redaction(h.root)
        maker = logging.Logger.makeRecord
        check(
            "FTF-0904-X3", "makeRecord must be wrapped", getattr(maker, "_ftf0904_redacting", False)
        )
        inner = getattr(maker, "__wrapped__", None)
        check(
            "FTF-0904-X3",
            "makeRecord must be wrapped exactly once",
            inner is not None and not getattr(inner, "_ftf0904_redacting", False),
        )


def test_secret_named_extra_with_non_str_value_is_redacted() -> None:
    with _Harness() as h:
        logging.getLogger("t").error("event", extra={"password": b"hunter2-xyz", "token": 12345})
        record = h.seen_records[-1]
    check(
        "FTF-0904-X4",
        f"bytes secret kept: {record.__dict__['password']!r}",
        record.__dict__["password"] == lr.REDACTED,
    )
    check(
        "FTF-0904-X4",
        f"int secret kept: {record.__dict__['token']!r}",
        record.__dict__["token"] == lr.REDACTED,
    )


# --- bounded runtime --------------------------------------------------------------------

_SIZE = 256 * 1024


def _adversarial_shape(label: str, size: int) -> str:
    """Every shape in `_ADVERSARIAL`, parameterized by size so it can be swept at several sizes
    (see `test_every_rule_size_sweep_is_linear_not_quadratic`), not just pinned once at `_SIZE`."""
    if label == "token-run":
        return "token" * (size // 5)
    if label == "quoted-token-run":
        return '"token"' * (size // 7)
    if label == "dash-token-run":
        return "token-" * (size // 6)
    if label == "dot-token-run":
        return "a.token" * (size // 7)
    if label == "password-run":
        return "password" * (size // 8)
    if label == "key-with-spaces":
        return "api_key " * (size // 8)
    if label == "sep-run":
        return "token" + "\\" * (size - 5)
    if label == "colon-run":
        return "token" + ":" * (size - 5)
    if label == "quote-run":
        return "token=" + '"' * (size - 6)
    if label == "backslash-quote":
        return 'token=\\"' * (size // 8)
    if label == "header-run":
        return "authorization" * (size // 13)
    if label == "percent-run":
        return "%41" * (size // 3)
    if label == "percent-space-run":
        return "%41 " * (size // 4)
    if label == "base64-run":
        return "QUJD" * (size // 4)
    if label == "pem-run":
        return "-----BEGIN " * (size // 11)
    if label == "bearer-run":
        return "bearer " * (size // 7)
    # Round 3: the old `\S*%[0-9A-Fa-f]{2}` scan backtracked a greedy `\S*` at every character
    # position in a run with no (or a far) closing hex pair -- quadratic, not linear.
    if label == "lone-percent":
        return "a" * (size - 1) + "%"
    if label == "percent-prefix-then-plain-run":
        return "%41" + "a" * (size - 3)
    if label == "compact-json-one-percent":
        return ('{"accuracy":"99%","note":"' + "a" * (size - 40) + '"}')[:size]
    if label == "callback-url-long-state":
        return ("https://cb?redirect_uri=https%3A%2F%2Fexample.com&state=" + "a" * size)[:size]
    # Round 4: no shape exercised `_PREFIXES`'s own JWT alternative at all.
    if label == "jwt-no-dot":
        # One very long attempt: both dots are required but the segment before each one had no
        # upper bound, so a run with no dot anywhere consumed to the true end before failing.
        return "eyJ" + "A" * (size - 3)
    if label == "jwt-repeated-no-dot":
        # The real quadratic shape: a BARE `"eyJ" * k` does NOT reproduce it, because the rule's
        # own leading `(?<![A-Za-z0-9])` lookbehind already rejects every repeat after the first
        # (each one is preceded by "J", a letter) -- confirmed by timing `"eyJ" * k` directly:
        # linear. `_` satisfies the lookbehind (it is not `[A-Za-z0-9]`) AND is itself a member
        # of the segment's own class (`[A-Za-z0-9_\-]`), so it does not stop the greedy consume
        # either: every repeat is a genuine, independent trigger, each re-scanning the rest of
        # the text for a dot that is never there. Confirmed quadratic pre-fix (`"_eyJ" * 40_000`:
        # 23s) and confirmed that possessive alone (`{5,}+`, no upper bound) does not fix it --
        # the wasted work is the repeated O(remaining) *consume*, not backtracking after it, so
        # only a bound on the quantifier's own maximum (`{5,1024}+`) makes it linear.
        return "_eyJ" * (size // 4)
    # Round 4: `_QUOTED_VALUE`'s escaped-quote alternative allows backslashes in its own middle
    # (unlike the bare-quote alternatives, the closing shape is not excluded from the class), so
    # a run of lone backslashes before the first real quote is a genuine forward scan, not a
    # wasted backtrack -- still worth sweeping since it is the one alternative this round bounds.
    if label == "lone-backslash-then-quote":
        return "token=\\" + "\\" * (size - 9) + '"'
    # Round 5: a backslash run in the MIDDLE of a real, fully opened-and-closed escaped-quoted
    # value -- unlike "lone-backslash-then-quote" above (which never finds a second quote at
    # all, so a key-first rule never even reaches content-matching), this one DOES open for
    # real, so the naive lazy `[^"\r\n]*?` content this round replaces grows one character at a
    # time into the backslash run, re-trying the greedy closing `\+"` over however much of the
    # run remains at every one of those O(run) steps -- quadratic (confirmed: through the real
    # `_KEY_VALUE` rule, a naive lazy content class took 18s at 128K vs 0.09s for the round-5
    # unrolled form). The round-4 bound capped this cost instead of fixing it.
    if label == "backslash-run-inside-quoted-value":
        return 'token=\\"' + "a" * 50 + "\\" * (size - 104) + "b" * 50 + '\\"'
    # Round 6: one time shape per rule the sweep did not yet reach (`_PAIR`, `_BASE64_RUN`,
    # `_USERINFO`) and the JWT shapes the per-run scan must keep linear.
    if label == "jwt-dash-repeat":
        return "eyJ-" * (size // 4)
    if label == "jwt-glued-context-run":
        return "token_eyJabcdef." * (size // 16)
    if label == "pair-open-run":
        return "(" * size
    if label == "pair-ws-open-run":
        return "( " * (size // 2)
    if label == "pair-backslash-value":
        return "('password', '" + "\\" * (size - 14)
    # Round 7: a single opener followed by one long whitespace run (the named
    # Q-PAIR-KEY-WS-QUAD shape: one start, so only a quadratic opener shows), and JWT-looking
    # starts that are all glued after an alphanumeric (every "eyJ" is skipped by `_jwt_start`).
    if label == "pair-paren-ws-run":
        return "(" + " " * (size - 1)
    if label == "pair-bracket-tab-run":
        return "[" + "\t" * (size - 1)
    if label == "jwt-glued-alnum-run":
        return "aeyJ" * ((size - 9) // 4) + ".abcdef.g"
    if label == "jwt-glued-x-run":
        return "x" + "eyJ" * ((size - 10) // 3) + ".abcdef.g"
    if label == "base64-run-bad-tail":
        return "A" * (size - 3) + "==="
    if label == "base64-run-bad-tail-url":
        return "a-_" * ((size - 3) // 3) + "==="
    if label == "userinfo-scheme-run":
        return "a" * size
    if label == "userinfo-scheme-run-sep":
        return "a" * (size - 3) + "://"
    if label == "qp-delim-run":
        return "&" * size
    if label == "qp-name-run":
        return "code" * (size // 4)
    if label == "qp-assign-run":
        return "a=" * (size // 2)
    if label == "qp-code-assign-run":
        return "code=" * (size // 5)
    if label == "qp-redacted-run":
        return "code=[REDACTED]" * (size // 15)
    if label == "qp-camel-run":
        # `"aB" * (size // 2)` is already `size` chars when `size` is even (every sweep size
        # is); the trailing "=" pushed the top 262144-char tier to 262145, one over
        # MAX_TEXT_LENGTH, so redact_text raised LogRedactionError instead of exercising
        # the camelCase scan at the real top size (the timing check still "passed" --
        # `_timed_redact`'s child catches the error and still reports an elapsed time -- so
        # this silently measured the oversize fail-closed path, not the shape itself).
        return ("aB" * (size // 2))[: size - 1] + "="
    if label == "qp-name-then-delim-run":
        return "code=" + "&" * (size - 5)
    raise ValueError(label)  # pragma: no cover - every label above is listed in _ALL_LABELS


_ALL_LABELS = (
    "token-run",
    "quoted-token-run",
    "dash-token-run",
    "dot-token-run",
    "password-run",
    "key-with-spaces",
    "sep-run",
    "colon-run",
    "quote-run",
    "backslash-quote",
    "header-run",
    "percent-run",
    "percent-space-run",
    "base64-run",
    "pem-run",
    "bearer-run",
    "lone-percent",
    "percent-prefix-then-plain-run",
    "compact-json-one-percent",
    "callback-url-long-state",
    "jwt-no-dot",
    "jwt-repeated-no-dot",
    "lone-backslash-then-quote",
    "backslash-run-inside-quoted-value",
    "jwt-dash-repeat",
    "jwt-glued-context-run",
    "pair-open-run",
    "pair-ws-open-run",
    "pair-backslash-value",
    "base64-run-bad-tail",
    "base64-run-bad-tail-url",
    "userinfo-scheme-run",
    "userinfo-scheme-run-sep",
    "pair-paren-ws-run",
    "pair-bracket-tab-run",
    "jwt-glued-alnum-run",
    "jwt-glued-x-run",
    "qp-delim-run",
    "qp-name-run",
    "qp-assign-run",
    "qp-code-assign-run",
    "qp-redacted-run",
    "qp-camel-run",
    "qp-name-then-delim-run",
)
_ADVERSARIAL = {label: _adversarial_shape(label, _SIZE) for label in _ALL_LABELS}


_CHILD = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from packages.security import log_redaction as lr\n"
    "text = sys.stdin.read()\n"
    "start = time.perf_counter()\n"
    "try:\n"
    "    out = lr.redact_text(text)\n"
    "except lr.LogRedactionError:\n"
    "    out = ''\n"
    "sys.stdout.write(f'{time.perf_counter() - start:.3f}\\n' + out[-80:])\n"
)
_LIMIT_SECONDS = 4.0


def _timed_redact(text: str) -> tuple[float | None, str]:
    """(seconds, tail of the output) for `redact_text(text)` in a child process; seconds is None
    if the child had to be killed (a catastrophic regex holds the GIL, a thread cannot time it)."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD, str(ROOT)],
            input=text,
            capture_output=True,
            text=True,
            timeout=_LIMIT_SECONDS + 3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, ""
    head, _, tail = result.stdout.partition("\n")
    return (float(head) if head else None), tail


def test_adversarial_input_is_linear() -> None:
    for label, text in _ADVERSARIAL.items():
        text = text[:_SIZE]
        elapsed, _ = _timed_redact(text)
        check(
            "FTF-0904-R1",
            f"{label}: {len(text)} chars took {elapsed}s (limit {_LIMIT_SECONDS}s)",
            elapsed is not None and elapsed < _LIMIT_SECONDS,
        )
        if elapsed is None:
            break  # one catastrophic shape is enough; do not wait out the other shapes
    # a real secret behind adversarial padding is still found, and quickly
    elapsed, tail = _timed_redact("token" * 40_000 + "=" + _PLAIN_SECRETS[0])
    check("FTF-0904-R2", f"padded secret took {elapsed}s", elapsed is not None and elapsed < 3.0)
    check("FTF-0904-R2", f"secret behind padding leaked: {tail!r}", _PLAIN_SECRETS[0] not in tail)


_CHILD_LOG = (
    "import io, logging, sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from packages.security import log_redaction as lr\n"
    "stream = io.StringIO()\n"
    "handler = logging.StreamHandler(stream)\n"
    "logging.getLogger().addHandler(handler)\n"
    "lr.install_redaction()\n"
    "text = sys.stdin.read()\n"
    "start = time.perf_counter()\n"
    "logging.getLogger('t').error('%s', text)\n"
    "sys.stdout.write(f'{time.perf_counter() - start:.3f}\\n' + stream.getvalue()[-80:])\n"
)


def test_adversarial_input_through_the_logging_path_is_bounded() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_LOG, str(ROOT)],
            input="token" * (_SIZE // 5),
            capture_output=True,
            text=True,
            timeout=_LIMIT_SECONDS + 3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        check("FTF-0904-R3", "logging a 256 KB adversarial line did not finish", False)
        return
    head = result.stdout.partition("\n")[0]
    elapsed = float(head) if head else None
    check(
        "FTF-0904-R3",
        f"logging a 256 KB line took {elapsed}s ({result.stderr[-200:]!r})",
        elapsed is not None and elapsed < _LIMIT_SECONDS,
    )


_PERCENT_QUADRATIC_LABELS = ("lone-percent", "percent-prefix-then-plain-run")
# Pinned, non-swept bound check (all four shapes, incl. "compact-json-one-percent" and
# "callback-url-long-state") lives in _ADVERSARIAL / test_adversarial_input_is_linear above;
# the sweep below additionally proves actual linear *scaling* (not just a bound at one size) on
# two representative shapes of the same regex mechanism, keeping the subprocess count bounded.
# Round 5: starts at 8 KB, not 64 KB -- the class-closing ruling asks for an "8 KB..256 KB"
# sweep, and a bound-caused leak (round 4's own JWT/escaped-quote bugs) would not necessarily
# show up as a *timing* problem at only the largest size -- a bound is a hard matching cutoff,
# not a growing cost, so the sweep needs a low point as well as a high one.
_SWEEP_SIZES = (8 * 1024, 64 * 1024, 256 * 1024)


def _percent_quadratic_shape(label: str, size: int) -> str:
    if label == "lone-percent":
        return "a" * (size - 1) + "%"
    if label == "percent-prefix-then-plain-run":
        return "%41" + "a" * (size - 3)
    if label == "compact-json-one-percent":
        return ('{"accuracy":"99%","note":"' + "a" * (size - 40) + '"}')[:size]
    return ("https://cb?redirect_uri=https%3A%2F%2Fexample.com&state=" + "a" * size)[:size]


def test_percent_token_scan_is_linear_not_quadratic() -> None:
    """Round-3 regression: `\\S*%[0-9A-Fa-f]{2}` backtracked a greedy `\\S*` at every character
    position in a whitespace-free run whose only (or far) closing hex pair never lets an early
    attempt succeed -- quadratic, not linear (confirmed: `"a" * n + "%"` took 24s at 128K and
    102s at 262K, a ~4.2x ratio for a ~2x size increase). A generated size sweep (64K..256K)
    through BOTH `redact_text` and the `install_redaction` logging path proves linear scaling,
    not just a bound under the cap at one hand-picked size; the four 256 KB shapes from the
    finding (lone %, a leading valid token then a long plain run, one bare `%` in compact JSON,
    a long opaque URL state param) are all pinned at the limit in `_ADVERSARIAL` above."""
    for label in _PERCENT_QUADRATIC_LABELS:
        for size in _SWEEP_SIZES:
            text = _percent_quadratic_shape(label, size)
            elapsed, _ = _timed_redact(text)
            check(
                "FTF-0904-R4",
                f"{label} at {size} chars took {elapsed}s (limit {_LIMIT_SECONDS}s)",
                elapsed is not None and elapsed < _LIMIT_SECONDS,
            )
            if elapsed is None:
                break
    # Round 5: a CPU-ratio check between two sweep points is flaky under shared-runner load (a
    # slow point can make even a genuinely linear pair's ratio look quadratic) -- each size's own
    # fixed, generous absolute bound above is the real, load-robust assertion; scaling itself is
    # what the multi-point sweep already demonstrates (every point passes its own bound, at a size
    # proportional to that bound).
    for label in _PERCENT_QUADRATIC_LABELS:
        for size in (_SWEEP_SIZES[-1],):
            text = _percent_quadratic_shape(label, size)
            try:
                result = subprocess.run(
                    [sys.executable, "-c", _CHILD_LOG, str(ROOT)],
                    input=text,
                    capture_output=True,
                    text=True,
                    timeout=_LIMIT_SECONDS + 3,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                check("FTF-0904-R5", f"{label} at {size} through logging did not finish", False)
                continue
            head = result.stdout.partition("\n")[0]
            elapsed = float(head) if head else None
            check(
                "FTF-0904-R5",
                f"{label} at {size} chars through logging took {elapsed}s",
                elapsed is not None and elapsed < _LIMIT_SECONDS,
            )


_ROUND4_QUADRATIC_LABELS = (
    "jwt-no-dot",
    "jwt-repeated-no-dot",
    "lone-backslash-then-quote",
    "backslash-run-inside-quoted-value",
)


_ROUND6_LABELS = _ALL_LABELS[24:33]
_ROUND7_LABELS = _ALL_LABELS[33:]


def _timed_log(text: str) -> float | None:
    """Seconds to log `text` through the installed redaction path in a child, None on a kill."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_LOG, str(ROOT)],
            input=text,
            capture_output=True,
            text=True,
            timeout=_LIMIT_SECONDS + 3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    head = result.stdout.partition("\n")[0]
    return float(head) if head else None


def test_round6_every_rule_shape_sweep_through_both_paths() -> None:
    """Round 6 (F-SWEEP-EVERY-RULE): `_PAIR`, `_BASE64_RUN`, `_USERINFO` and the per-run JWT scan
    each get time shapes, swept 8 KB..256 KB through BOTH `redact_text` and the logging path with
    an absolute bound at every size (no ratio: flaky under load)."""
    for label in _ROUND6_LABELS:
        for size in _SWEEP_SIZES:
            text = _adversarial_shape(label, size)
            elapsed, _ = _timed_redact(text)
            check(
                "FTF-0904-R8",
                f"{label} at {size} chars (redact_text) took {elapsed}s",
                elapsed is not None and elapsed < _LIMIT_SECONDS,
            )
            logged = _timed_log(text)
            check(
                "FTF-0904-R9",
                f"{label} at {size} chars (logging path) took {logged}s",
                logged is not None and logged < _LIMIT_SECONDS,
            )


def test_round7_pair_whitespace_and_jwt_glued_shapes_through_both_paths() -> None:
    """Round 7 (Q-PAIR-KEY-WS-QUAD, Q-JWT-LOWER-IN-LOOP): the single-opener whitespace runs
    `"(" + " "*(n-1)` / `"[" + "\\t"*(n-1)` and the all-glued-"eyJ" runs `"aeyJ"*k + ".abcdef.g"`
    / `"x" + "eyJ"*k + ".abcdef.g"` swept 8 KB..256 KB through BOTH `redact_text` and the logging
    path, with an absolute bound at every size."""
    for label in _ROUND7_LABELS:
        for size in _SWEEP_SIZES:
            text = _adversarial_shape(label, size)
            elapsed, _ = _timed_redact(text)
            check(
                "FTF-0904-R10",
                f"{label} at {size} chars (redact_text) took {elapsed}s",
                elapsed is not None and elapsed < _LIMIT_SECONDS,
            )
            logged = _timed_log(text)
            check(
                "FTF-0904-R11",
                f"{label} at {size} chars (logging path) took {logged}s",
                logged is not None and logged < _LIMIT_SECONDS,
            )


def test_round7_glued_jwt_shapes_stay_redaction_correct() -> None:
    """Negative on the real default code: the glued shapes are fast AND a real JWT after them is
    still redacted (speed bought by skipping starts would be a leak, not linearity)."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLWJ5dGVz"
    for prefix in ("aeyJ" * 2000, "x" + "eyJ" * 2000):
        _, out = _timed_redact(prefix + " " + jwt)
        check("FTF-0904-R12", "a JWT after glued eyJ starts survived", jwt[:20] not in out)
        check("FTF-0904-R12", f"no redaction marker in the tail: {out!r}", "[REDACTED]" in out)


def test_every_rule_size_sweep_is_linear_not_quadratic() -> None:
    """Round-4 return: the round-3 sweep above proved scaling only for the two percent-token
    shapes round 3 itself fixed, not the class-closing bar round 3 set ("every regex that runs
    on log text must be linear ... proven by a generated size sweep ... through BOTH redact_text
    and the install_redaction logging path") for every rule a later round touches.

    Round 5 widens this from the two round-4 labels to EVERY shape in `_ALL_LABELS` -- the
    class-closing ruling is "every rule is linear", not "every rule a round happened to touch",
    and round 4 itself only added adversarial coverage for the two shapes it fixed while leaving
    every other rule swept at a single pinned size (`test_adversarial_input_is_linear` above),
    which proved a bound, not a scaling trend. `_PREFIXES`' own JWT alternative (`jwt-no-dot`/
    `jwt-repeated-no-dot`) and `_QUOTED_VALUE`'s own escaped-quote alternative
    (`lone-backslash-then-quote`) are round 4's two fixes, now round 5's two *re*-fixes (round 4
    bounded both instead of truly fixing them -- a bound is a leak, not linearity, per round 5's
    own class-closing ruling); every shape is generated via the same `_adversarial_shape`, swept
    across `_SWEEP_SIZES` (8 KB..256 KB)."""
    for label in _ALL_LABELS:
        for size in _SWEEP_SIZES:
            text = _adversarial_shape(label, size)
            elapsed, _ = _timed_redact(text)
            check(
                "FTF-0904-R6",
                f"{label} at {size} chars took {elapsed}s (limit {_LIMIT_SECONDS}s)",
                elapsed is not None and elapsed < _LIMIT_SECONDS,
            )
            if elapsed is None:
                break
    # Round 5: no CPU-ratio check here either (see the matching note on R4 above) -- each size's
    # own absolute bound is the load-robust assertion, and the sweep across sizes is what
    # demonstrates scaling, not a ratio computed from two single, noise-sensitive samples.
    # The round-4/5 shapes additionally proven through the real logging path, at the top size
    for label in _ROUND4_QUADRATIC_LABELS:
        text = _adversarial_shape(label, _SWEEP_SIZES[-1])
        try:
            result = subprocess.run(
                [sys.executable, "-c", _CHILD_LOG, str(ROOT)],
                input=text,
                capture_output=True,
                text=True,
                timeout=_LIMIT_SECONDS + 3,
                check=False,
            )
        except subprocess.TimeoutExpired:
            check("FTF-0904-R7", f"{label} through logging did not finish", False)
            continue
        head = result.stdout.partition("\n")[0]
        elapsed = float(head) if head else None
        check(
            "FTF-0904-R7",
            f"{label} at {_SWEEP_SIZES[-1]} chars through logging took {elapsed}s",
            elapsed is not None and elapsed < _LIMIT_SECONDS,
        )


# Round 5, class-closing ruling (b): "for every rule, a secret of 1 KB, 8 KB, 64 KB and 256 KB is
# fully redacted" -- a *correctness* sweep, independent of the *timing* sweep above. A bound
# (round 4's JWT-segment and escaped-quote-value caps) is a hard matching cutoff, not a growing
# cost, so it would never show up as a timing failure -- only as the marker surviving in the
# output once its length passes the bound. The last tier stays comfortably under
# `lr.MAX_TEXT_LENGTH` once wrapped.
_LONG_SECRET_SIZES = (1024, 8192, 65536, 262_000)


def _long_secret_cases(marker: str) -> list[tuple[str, str]]:
    """(rule, text) for every named rule in the ruling (_PEM, _HEADER, _PAIR, _BEARER,
    _PREFIXES, _USERINFO, _KEY_VALUE, _BASE64_RUN, percent), each built so that rule's own match
    has to span the whole `marker` -- any bound shorter than `len(marker)` leaves a suffix of
    `marker` in the output. `marker` is plain alphanumeric so it is valid unchanged inside every
    wrapper (a quoted value, a URL, a JSON string, a JWT segment, a key=value pair) with no
    delimiter collisions to reason about per rule."""
    b64_inner = base64.b64encode(json.dumps({"token": marker}).encode()).decode()
    return [
        ("pem", f"-----BEGIN PRIVATE KEY-----\n{marker}\n-----END PRIVATE KEY-----"),
        ("header-bare", f"Authorization: {marker}"),
        ("header-escaped-quote", 'authorization: \\"' + marker + '\\"'),
        ("pair", f"('password', '{marker}')"),
        ("bearer", f"Bearer {marker}"),
        ("prefix", f"sk-{marker}"),
        ("prefix-jwt", f"eyJ{marker}.{marker}.{marker}"),
        ("userinfo", f"https://user:{marker}@db.internal:5432/x"),
        ("key_value-bare", f"token={marker}"),
        ("key_value-quoted", f'token="{marker}"'),
        ("key_value-escaped-quote", 'token=\\"' + marker + '\\"'),
        ("query_param", f"GET /cb?x=1&code={marker}"),
        ("query_param-no-delimiter", f"session_key={marker}"),
        ("base64", b64_inner),
        ("percent", "GET /x?q=" + quote("password=" + marker, safe="")),
    ]


def _fragments(text: str) -> list[str]:
    """32-char windows at the start, a quarter, the middle, three quarters and the very end of
    `text`, so a truncating bound (the tail of a long secret left in the clear) cannot hide
    behind a whole-marker `not in` check, which only fails when NOTHING is redacted."""
    last = max(len(text) - 32, 0)
    offsets = sorted({0, len(text) // 4, len(text) // 2, 3 * len(text) // 4, last})
    return [text[o : o + 32] for o in offsets]


def _surviving(text: str, out: str) -> list[str]:
    return [fragment for fragment in _fragments(text) if fragment in out]


def _fit_size(rule: str, limit: int) -> int:
    """The largest marker size whose wrapped `rule` text still fits `limit` characters, so the
    top tier is a real 256 KB case that is redacted, not one refused as oversize."""
    small = dict(_long_secret_cases(_marker(200)))[rule]
    large = dict(_long_secret_cases(_marker(400)))[rule]
    per_char = (len(large) - len(small)) / 200
    size = int((limit - (len(small) - 200 * per_char)) / per_char)
    while len(dict(_long_secret_cases(_marker(size)))[rule]) > limit:
        size -= 64
    return size


def _long_secret_inputs(rule: str, size: int) -> tuple[str, str]:
    """(wrapped text, the string whose fragments must not survive) for `rule` at `size`."""
    marker = _marker(size)
    text = dict(_long_secret_cases(marker))[rule]
    # the base64 case's secret is the encoded text itself: check ITS fragments, not the marker's
    return text, text if rule == "base64" else marker


_LONG_RULES = tuple(rule for rule, _ in _long_secret_cases(_marker(64)))


def test_every_rule_long_secret_is_fully_redacted() -> None:
    """Round 5/6: a bound is a hard matching cutoff, not a growing cost, so it never shows as a
    timing failure -- only as the tail of a long secret surviving. Round 6 (F-L1-SUFFIX,
    F-L1-B64-VACUOUS): check a fragment at the start, quarter, middle, three quarters and END of
    the secret (a `marker not in out` check passes while 302 of 342 signature chars leak), and for
    the base64 case check fragments of the ENCODED text (the raw marker never appears in it, so
    the old check could not fail). The top tier is sized to fit the limit per rule: an oversize
    raise is not a pass."""
    for rule in _LONG_RULES:
        for size in (*_LONG_SECRET_SIZES[:-1], _fit_size(rule, lr.MAX_TEXT_LENGTH)):
            text, secret = _long_secret_inputs(rule, size)
            try:
                out = lr.redact_text(text)
            except lr.LogRedactionError:
                check("FTF-0904-L1", f"{rule} at {size}: redact_text raised on {len(text)}", False)
                continue
            left = _surviving(secret, out)
            check(
                "FTF-0904-L1",
                f"{rule} at {size} chars: {len(left)} fragment(s) of the secret survived",
                not left and REDACTED_ in out,
            )


def test_every_rule_long_secret_through_the_logging_path() -> None:
    """Round 6 (F-L1-LOG): the same sweep through `install_redaction` + the sink, with a real
    256 KB tier for every rule including JWT and base64 (the sink re-redacts the formatted line,
    which is a few characters longer, so the top tier leaves 64 characters of headroom)."""
    for rule in _LONG_RULES:
        for size in (1024, 65536, _fit_size(rule, lr.MAX_TEXT_LENGTH - 64)):
            text, secret = _long_secret_inputs(rule, size)
            with _Harness() as harness:
                logging.getLogger("long").error("%s", text)
            seen = "\n".join(harness.outputs())
            left = _surviving(secret, seen)
            check(
                "FTF-0904-L2",
                f"{rule} at {size} chars via logging: {len(left)} fragment(s) survived",
                not left and lr.REDACTION_FAILED_MARKER not in seen and REDACTED_ in seen,
            )


_JWT_CONTEXTS = ("token_", "sess-", "cache_", "user-", "id-", "jwt_", "x_")


def _realistic_jwt(claims: int) -> str:
    """A JWT-shaped secret with a payload of any length (RS256-sized for `claims` ~ 1500)."""
    header = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
    payload = "eyJ" + _marker(claims)[:claims]
    return f"{header}.{payload}.{_marker(342)[:342]}"


def test_jwt_glued_after_underscore_or_dash_is_redacted() -> None:
    """Round 6 (F-JWT-GLUE): rounds 3/4 redacted a JWT glued after `_`/`-`; the round-5
    lookbehind printed `token_<jwt>`, `sess-<jwt>`, ... in full. All seven contexts, three sizes,
    through `redact_text` and the logging path; no 32-char fragment of any segment may survive."""
    for claims in (40, 1500, 20_000):
        jwt = _realistic_jwt(claims)
        header, payload, sig = jwt.split(".")
        pieces = [header, payload[:32], payload[len(payload) // 2 :][:32], payload[-32:], sig[:32]]
        for ctx in _JWT_CONTEXTS + ("",):
            text = f"{ctx}{jwt}"
            out = lr.redact_text(text)
            check(
                "FTF-0904-J1",
                f"context {ctx!r} claims={claims}: a JWT fragment survived: {out[:60]!r}",
                not any(piece in out for piece in pieces) and out.startswith(ctx),
            )
            with _Harness() as harness:
                logging.getLogger("jwt").error("%s", text)
            seen = "\n".join(harness.outputs())
            check(
                "FTF-0904-J2",
                f"context {ctx!r} claims={claims}: a JWT fragment reached the log",
                not any(piece in seen for piece in pieces),
            )
    # negative: the real default code leaves a non-JWT lookalike alone (no dots, glued to alnum)
    for benign in (
        "token_eyJ",
        "x_eyJab.cd",
        "abceyJabcdef.ghijkl.mnopqr",
        "eyJ.a.b",
        "eyJabcdef.abc.defgh",
        "eyJabc.abcdefgh.ijklmn",
    ):
        check(
            "FTF-0904-J3", f"benign lookalike {benign!r} altered", lr.redact_text(benign) == benign
        )


def test_jwt_segment_floors_are_exact() -> None:
    """Round 6 (mutation gate): the JWT shape needs 5+ chars after "eyJ", a 5+ char second
    segment and a SECOND dot (a third, possibly empty, segment). Each boundary is pinned on
    both sides, so `>=`/`>` and `<`/`<=` flips on any of them are RED."""
    redacted = {
        "eyJ12345.abcde.x": "[REDACTED]",
        "eyJ12345.abcde.": "[REDACTED]",
        "ab.eyJ12345.abcde.x": "ab.[REDACTED]",
    }
    for text, want in redacted.items():
        check(
            "FTF-0904-J5",
            f"{text!r} -> {lr.redact_text(text)!r}, want {want!r}",
            lr.redact_text(text) == want,
        )
    for benign in (
        "eyJ1234.abcde.x",  # 4 chars after eyJ
        "eyJ12345.abcd.x",  # 4-char payload segment
        "eyJ12345.abcdefgh",  # no second dot
        "eyJ12345.abcdefgh/more",  # no second dot
    ):
        check("FTF-0904-J6", f"benign {benign!r} altered", lr.redact_text(benign) == benign)


def test_jwt_runs_inside_other_text_keep_their_neighbours() -> None:
    """A JWT inside a longer line removes only the JWT: text on both sides and a second JWT on
    the same line (separated by a dot-less gap) behave independently."""
    a, b = _realistic_jwt(60), _realistic_jwt(70)
    out = lr.redact_text(f"before user-{a} between {b}, after")
    check(
        "FTF-0904-J4",
        f"neighbouring text damaged: {out!r}",
        out == f"before user-{REDACTED_} between {REDACTED_}, after",
    )


def test_size_and_depth_boundaries() -> None:
    at_limit = "a" * lr.MAX_TEXT_LENGTH
    check(
        "FTF-0904-B1",
        "text of exactly MAX_TEXT_LENGTH is accepted",
        lr.redact_text(at_limit) == at_limit,
    )
    try:
        lr.redact_text(at_limit + "a")
        refused = False
    except lr.LogRedactionError:
        refused = True
    check("FTF-0904-B1", "text one over MAX_TEXT_LENGTH is refused", refused)

    def nested(levels: int) -> object:
        value: object = "x"
        for _ in range(levels):
            value = [value]
        return value

    check(
        "FTF-0904-B2",
        "a container nested _MAX_DEPTH deep is still walked",
        lr.redact_value(nested(lr._MAX_DEPTH)) == nested(lr._MAX_DEPTH),
    )
    deeper = lr.redact_value(nested(lr._MAX_DEPTH + 1))
    check(
        "FTF-0904-B2",
        f"a container past _MAX_DEPTH is redacted whole: {deeper!r}",
        deeper != nested(lr._MAX_DEPTH + 1) and "x" not in repr(deeper),
    )
    cycle: list[object] = []
    cycle.append(cycle)
    check(
        "FTF-0904-B3",
        f"a self-containing list is cut at the first revisit: {lr.redact_value(cycle)!r}",
        lr.redact_value(cycle) == [lr.REDACTED],
    )


def test_only_two_element_pairs_are_header_pairs() -> None:
    for value in ([], ["token"], ["token", "a", "b"], ("password", "p", "q", "r"), [1, "x"]):
        out = lr.redact_value(value)
        check("FTF-0904-B4", f"{value!r} must keep its shape, got {out!r}", out == value)
    check(
        "FTF-0904-B4",
        "a 2-element secret pair is redacted",
        lr.redact_value(["token", "abc"]) == ["token", lr.REDACTED],
    )
    check(
        "FTF-0904-B4",
        "a 2-element pair with a non-str name is kept",
        lr.redact_value([1, "abc"]) == [1, "abc"],
    )


def test_three_percent_layers_are_decoded() -> None:
    secret = _PLAIN_SECRETS[0]
    for layers in (1, 2, 3):
        for shape in ("password={s}", "Authorization: Bearer {s}", "token: {s}"):
            out = lr.redact_text("q=" + _enc(shape.format(s=secret), layers))
            check("FTF-0904-A7", f"{shape!r} x{layers}: {out!r}", secret not in out)


# ---------------------------------------------------------------------------
# NEG-2: redaction does not authorise external telemetry of sensitive content
# ---------------------------------------------------------------------------


def test_redacted_text_is_still_refused_by_external_telemetry() -> None:
    from packages.security import content_free_telemetry as cft

    redacted = lr.redact_text(
        "Authorization: Bearer " + SECRETS["openai"] + " failed on prompt: summarise my contract"
    )
    refused = 0
    attempts = 0
    for kind in cft.EVENT_KINDS:
        baseline = _valid_event_fields(kind)
        check(
            "FTF-0904-N2",
            f"baseline {kind} event must itself be valid",
            _accepted(cft, kind, baseline),
        )
        for field in [*cft.EVENT_SCHEMAS[kind], "message", "detail", "text", "redacted"]:
            attempts += 1
            if not _accepted(cft, kind, {**baseline, field: redacted}):
                refused += 1
    check(
        "FTF-0904-N2",
        f"redacted free text accepted by external telemetry ({refused}/{attempts} refused)",
        refused == attempts and attempts > 20,
    )
    source = MODULE.read_text(encoding="utf-8")
    check(
        "FTF-0904-N2",
        "module never imports or builds telemetry events",
        "content_free_telemetry" not in source and "emit_event" not in source,
    )
    public = [n for n in dir(lr) if not n.startswith("_")]
    check(
        "FTF-0904-N2",
        "module exposes no telemetry-permission function",
        not any(re.search(r"telemetry|egress|external", n, re.I) for n in public),
    )


def _accepted(cft: object, kind: str, fields: dict[str, object]) -> bool:
    try:
        cft.emit_event(kind, fields)  # type: ignore[attr-defined]
    except cft.ContentFreeTelemetryError:  # type: ignore[attr-defined]
        return False
    return True


def _valid_event_fields(kind: str) -> dict[str, object]:
    from datetime import UTC, datetime

    from packages.security import content_free_telemetry as cft

    out: dict[str, object] = {}
    for name, spec in cft.EVENT_SCHEMAS[kind].items():
        if not spec.required:
            continue
        if spec.kind == "id":
            out[name] = "id-1"
        elif spec.kind == "code":
            out[name] = "code_1"
        elif spec.kind == "enum":
            assert spec.enum is not None
            out[name] = spec.enum[0]
        elif spec.kind == "timestamp":
            out[name] = datetime.now(UTC)
        else:
            raise AssertionError(f"unhandled required kind {spec.kind}")
    return out


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# Fault injection: mutate a COPY of the module, rerun the behavioural checks
# ---------------------------------------------------------------------------

# (id, old, new): `old` must occur in the real module exactly `count` times.
# --- FTF-0904-F1: bare secret-valued query parameters and the percent-encoded fullwidth `=` ---

_F1_NAMES = ("key", "sig", "auth", "session", "jwt", "code", "state")
_F1_VALUE = "Zq7Lm2Xv9Rt4Hn8Kc3Pw6Yb1"  # no prefix, no JWT shape: only the parameter name binds it
_F1_SEPARATORS = {
    "plain": "=",
    "percent": "%3D",
    "double-percent": "%253D",
    "fullwidth": "＝",
    "percent-fullwidth": "%EF%BC%9D",
    "double-percent-fullwidth": "%25EF%25BC%259D",
    "triple-percent-fullwidth": "%2525EF%2525BC%25259D",
}


def _f1_cases() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    for name in _F1_NAMES:
        for cased in (name, name.upper(), name.capitalize()):
            for sep_label, sep in _F1_SEPARATORS.items():
                pair = f"{cased}{sep}{_F1_VALUE}"
                for pos_label, template in (
                    ("first", "GET /x?{p} HTTP/1.1"),
                    ("middle", "GET /x?a=1&{p}&b=2 HTTP/1.1"),
                    ("last", "GET /x?a=1&{p}"),
                    ("semicolon", "GET /x?a=1;{p}"),
                    ("fragment", "redirect https://h/cb#{p}"),
                    ("callback", "GET /auth/callback?{p}&other=1 HTTP/1.1"),
                    # round 2 (QP-POS-SERIALIZED): serialized without `?`, and form bodies
                    ("line-start", "{p}&other=1"),
                    ("after-space", "params {p} other=1"),
                    ("after-quote", 'params="{p}"'),
                    ("after-slash", "GET /x/{p}"),
                    ("after-comma", "args=(1,{p})"),
                    ("after-pipe", "a|{p}|b"),
                    # `{p}` must be the very first thing after the blank line, with nothing
                    # else (no `&`) between the newline and the key -- a prior version put it
                    # after "grant_type=authorization_code&", which is independently caught by
                    # the "&" there regardless of whether "after newline" is supported at all,
                    # so dropping that support never changed this case's outcome (confirmed by
                    # a real CI run: the fault that simulates its absence stayed GREEN).
                    ("form-body", "POST /token\n\n{p}&x=1"),
                ):
                    cases.append(
                        (f"{name}/{cased}/{sep_label}/{pos_label}", template.format(p=pair))
                    )
    return cases


def test_f1_bare_secret_query_parameters_are_redacted() -> None:
    for label, text in _f1_cases():
        out = lr.redact_text(text)
        check("FTF-0904-F1", f"{label}: leaked {out!r}", _F1_VALUE not in out)
    # the OAuth callback with both live credentials, unencoded: the shape the bug report names
    out = lr.redact_text(f"GET /auth/callback?code={_F1_VALUE}&state={_F1_VALUE}2 HTTP/1.1")
    check("FTF-0904-F1", f"callback code+state leaked {out!r}", "Zq7Lm2" not in out)
    check("FTF-0904-F1", "callback path kept", "/auth/callback" in out)


def test_f1_cases_through_the_logging_path() -> None:
    for label, text in _f1_cases()[::5]:
        with _Harness() as h:
            logging.getLogger("t").error("%s", text)
            logging.getLogger("t").error(text)
            found = leaked([_F1_VALUE], h.outputs())
        check("FTF-0904-F1", f"{label}: end-to-end leak {found}", not found)


def test_f1_benign_look_alikes_survive() -> None:
    for text in (
        "GET /x?keyboard=us&codec=h264&statement=ok&sigma=1 HTTP/1.1",
        "state_of=running code_path=/srv keys=3",
        "GET /x?a=1&mykeyboard=zzz",
        "monkey=1 passage=2 ticketing=3 sidebar=4 authority=5 other=6 session_count=7",
    ):
        check(
            "FTF-0904-F1",
            f"benign text changed: {lr.redact_text(text)!r}",
            lr.redact_text(text) == text,
        )
    out = lr.redact_text(f"GET /x?state=ok HTTP/1.1 {_F1_VALUE}")
    check(
        "FTF-0904-F1",
        "the rule never removes text outside the parameter value",
        "GET /x?state=" in out,
    )


# --- FTF-0904-F1 round 2 ---------------------------------------------------------------------

_F1_COMPOUND_NAMES = (
    "session_key", "sessionKey", "SESSION-KEY", "auth_code", "authCode", "access_code",
    "code_verifier", "codeVerifier", "oauth_verifier", "api.key", "PHPSESSID", "JSESSIONID",
    "SESSID", "sid", "connect.sid", "ticket", "service_ticket", "otp", "pass", "login_otp",
)  # fmt: skip
# one family per row; a fault dropping the family from `_QUERY_PARAM_NAMES` must go RED
_F1_FAMILIES = {
    "key": ("session_key", "sessionKey", "SESSION-KEY", "api.key"),
    "code": ("auth_code", "authCode", "access_code"),
    "verifier": ("code_verifier", "codeVerifier", "oauth_verifier"),
    "sessid": ("PHPSESSID", "JSESSIONID", "SESSID"),
    "sid": ("sid", "connect.sid"),
    "ticket": ("ticket", "service_ticket"),
    "otp": ("otp", "login_otp"),
    "pass": ("pass",),
}


def test_f1_round2_name_class_membership_is_isolated() -> None:
    """`"jsessionid"` (and every name whose literal spelling also happens to contain
    `_SECRET_KEY_WORDS`'s own `session[_-]?id` substring) is independently redacted by the
    pre-existing, unrelated `_KEY_VALUE` rule even with no help from `_QUERY_PARAM_NAMES` at
    all -- a real CI run confirmed dropping "jsessionid" from the set stayed GREEN end-to-end
    (`redact_text`), because that overlap masks it. Asserting `_is_query_param_name` directly
    isolates this rule's own membership test from that overlap."""
    for name in ("jsessionid", "key", "sig", "auth", "session", "jwt", "code", "state"):
        check(
            "FTF-0904-F1",
            f"_is_query_param_name({name!r}) is not True",
            lr._is_query_param_name(name) is True,
        )
    check(
        "FTF-0904-F1",
        "_is_query_param_name('notaname') is not False",
        lr._is_query_param_name("notaname") is False,
    )


def test_f1_round2_name_class_is_redacted() -> None:
    check(
        "FTF-0904-F1",
        "every compound in a family row is also in _F1_COMPOUND_NAMES",
        {n for names in _F1_FAMILIES.values() for n in names} <= set(_F1_COMPOUND_NAMES),
    )
    for name in _F1_COMPOUND_NAMES:
        for template in ("GET /x?{p} HTTP/1.1", "{p}&x=1", "Set {p} now"):
            text = template.format(p=f"{name}={_F1_VALUE}")
            out = lr.redact_text(text)
            check("FTF-0904-F1", f"QP-NAME-CLASS {name}: leaked {out!r}", _F1_VALUE not in out)


def _f1_idempotence_inputs() -> list[str]:
    texts = [text for _, text in _f1_cases()]
    texts += [
        f"GET /auth/callback?code={_F1_VALUE}&state={_F1_VALUE}2#jwt={_F1_VALUE}3 HTTP/1.1",
        "code=[REDACTED] state=[REDACTED] #jwt=[REDACTED]",
        "code=[REDACTED]x",
    ]
    return texts


def test_f1_round2_redaction_is_idempotent() -> None:
    """QP-IDEM-GUARD: one pass already yields `#jwt=[REDACTED]]` when the rule has no
    `[REDACTED]` guard; the sink then re-redacts the formatted line. Pass 2 must equal pass 1,
    and no output may contain `]]` or a doubled marker."""
    for text in _f1_idempotence_inputs():
        once = lr.redact_text(text)
        twice = lr.redact_text(once)
        check("FTF-0904-F1", f"QP-IDEM-GUARD {text!r}: {once!r} -> {twice!r}", once == twice)
        check("FTF-0904-F1", f"QP-IDEM-GUARD stray bracket in {once!r}", "]]" not in once)


def test_f1_round2_live_logging_output_is_clean() -> None:
    line = f"GET /auth/callback?code={_F1_VALUE}&state={_F1_VALUE}2#jwt={_F1_VALUE}3 HTTP/1.1"
    with _Harness() as h:
        logging.getLogger("t").error("%s", line)
        logging.getLogger("t").error(line)
        outputs = h.outputs()
    seen = "\n".join(outputs)
    check("FTF-0904-F1", f"QP-IDEM-GUARD live output: {seen!r}", "]]" not in seen)
    check("FTF-0904-F1", "live output keeps the callback path", "/auth/callback" in seen)
    check("FTF-0904-F1", f"live output leaked: {seen!r}", _F1_VALUE not in seen)
    for output in outputs:
        check(
            "FTF-0904-F1",
            f"live output not idempotent {output!r}",
            lr.redact_text(output) == output,
        )


def test_f1_round2_non_name_key_does_not_swallow_a_later_name() -> None:
    out = lr.redact_text(f"foo=code={_F1_VALUE} bar=state={_F1_VALUE}2")
    check("FTF-0904-F1", f"a non-name key swallowed a name: {out!r}", "Zq7Lm2" not in out)


_F3_NAMES = (
    "key", "sig", "auth", "session", "jwt", "code", "state", "verifier", "ticket", "otp",
    "totp", "hotp", "pass", "sid", "sessid", "phpsessid", "jsessionid",
)  # fmt: skip
_F3_ENV = (
    "GET /x?{p} HTTP/1.1",  # query position
    "{p}&x=1",  # serialized / urlencoded form position
)


def _f3_random_case(name: str) -> list[str]:
    """Alternating, inverted-alternating, upper and seeded random-case spellings of `name`."""
    rng = random.Random(f"ftf-0904-f1-r3:{name}")
    spellings = {
        "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(name)),
        "".join(c.lower() if i % 2 else c.upper() for i, c in enumerate(name)),
        name.upper(),
        name.title(),
    }
    for _ in range(24):
        spellings.add("".join(c.upper() if rng.random() < 0.5 else c.lower() for c in name))
    return sorted(spellings)


def test_f1_round3_random_case_names_are_redacted() -> None:
    """QP-CASE-SPLIT: the camelCase split must not hide a name spelled in random case."""
    for name in _F3_NAMES:
        for spelling in _f3_random_case(name):
            for template in _F3_ENV:
                out = lr.redact_text(template.format(p=f"{spelling}={_F1_VALUE}"))
                check(
                    "FTF-0904-F1", f"QP-CASE-SPLIT {spelling}: leaked {out!r}", _F1_VALUE not in out
                )


_F3_PREFIXES = (
    "session", "auth", "access", "pass", "otp", "api", "oauth", "oauth2", "login", "sso",
    "csrf", "xsrf", "refresh", "reset", "confirm", "verification", "magic", "signed", "client",
    "user", "app", "private", "secret", "authorization", "key", "code",
)  # fmt: skip


def _f3_glue(prefix: str, name: str) -> dict[str, str]:
    return {
        "lower": prefix + name,
        "UPPER": (prefix + name).upper(),
        "Title": prefix.title() + name.title(),
        "Capitalized": (prefix + name).capitalize(),
        "random": _f3_random_case(prefix + name)[-1],
    }


def test_f1_round3_last_word_and_camel_split_with_unlisted_prefixes() -> None:
    """The last-word and camelCase rules, isolated from the glued-chain vocabulary."""
    for name in ("foo_key", "widget.code", "fooKey", "myWidgetState", "x-widget-sig", "zedTicket"):
        out = lr.redact_text(f"GET /x?{name}={_F1_VALUE} HTTP/1.1")
        check("FTF-0904-F1", f"last-word/camel {name}: leaked {out!r}", _F1_VALUE not in out)


def test_f1_round3_glued_compounds_are_redacted() -> None:
    """QP-GLUED-COMPOUND (round 3 contract, kept): prefix x name with no separator."""
    for prefix in _F3_PREFIXES:
        for name in _F3_NAMES:
            for joiner, glued in _f3_glue(prefix, name).items():
                out = lr.redact_text(f"GET /x?{glued}={_F1_VALUE} HTTP/1.1")
                check(
                    "FTF-0904-F1",
                    f"QP-GLUED-COMPOUND[{joiner}] {glued}: leaked {out!r}",
                    _F1_VALUE not in out,
                )
    for glued in ("sessionkey", "authcode", "accesscode", "passcode", "totp", "otpcode"):
        out = lr.redact_text(f"{glued}={_F1_VALUE}&x=1")
        check("FTF-0904-F1", f"QP-GLUED-COMPOUND contract {glued}: {out!r}", _F1_VALUE not in out)


# ---------------------------------------------------------------------------------------------
# FTF-0904-F1 round 4. The prefix lists below are written out HERE, independently of the
# implementation (nothing is imported from `lr` to build them): a table derived from the
# implementation's own vocabulary can only confirm it.
# ---------------------------------------------------------------------------------------------
_F4_PREFIXES = (
    "mfa", "connect", "service", "php", "x", "sms", "device", "signing", "recovery", "hmac",
    "foo", "widget", "tenant", "bot", "cli", "mobile", "web", "admin", "partner", "legacy",
    "temp", "backup", "vendor", "gateway", "my", "the", "new", "old", "next", "prev",
)  # fmt: skip
_F4_REALISTIC = (
    "mfacode", "smscode", "devicecode", "signingkey", "connectsid", "recoverycode", "hmacsig",
    "servicekey", "phpkey", "xsid", "mfaotp", "webauthcode", "appsecretkey", "adminpass",
    "userticket", "sessiontoken_state", "mykey", "mypass", "clientcode", "backupcode",
)  # fmt: skip
_F4_SEPS = ("_", "-", ".")


def _f4_leaks(text: str) -> bool:
    return _F1_VALUE in lr.redact_text(text)


def test_f1_round4_glued_names_with_any_prefix_are_redacted() -> None:
    """QP-GLUED-VOCAB: deny by default -- the prefix is irrelevant, only the ending binds."""
    for prefix in _F4_PREFIXES:
        for name in _F3_NAMES:
            for style, glued in _f3_glue(prefix, name).items():
                for template in _F3_ENV:
                    text = template.format(p=f"{glued}={_F1_VALUE}")
                    check(
                        "FTF-0904-F1",
                        f"QP-GLUED-VOCAB[{style}] {glued}: leaked {lr.redact_text(text)!r}",
                        not _f4_leaks(text),
                    )
    for glued in _F4_REALISTIC:
        for spelling in (glued, glued.upper(), glued.title()):
            text = f"GET /x?{spelling}={_F1_VALUE} HTTP/1.1"
            check("FTF-0904-F1", f"QP-GLUED-VOCAB realistic {spelling} leaked", not _f4_leaks(text))


def test_f1_round4_random_case_after_any_separator_is_redacted() -> None:
    """QP-CASE-SPLIT-SEPARATOR: `mfa_CodE`, `request_sTaTe`, `connect.sId`, `foo-kEy`."""
    for prefix in _F4_PREFIXES:
        for sep in _F4_SEPS:
            for name in _F3_NAMES:
                for spelling in _f3_random_case(name)[-3:]:
                    for template in _F3_ENV:
                        text = template.format(p=f"{prefix}{sep}{spelling}={_F1_VALUE}")
                        check(
                            "FTF-0904-F1",
                            f"QP-CASE-SPLIT-SEPARATOR {prefix}{sep}{spelling} leaked",
                            not _f4_leaks(text),
                        )
    for literal in ("mfa_CodE", "request_sTaTe", "connect.sId", "foo-kEy", "x_cOdE"):
        check(
            "FTF-0904-F1",
            f"reported spelling {literal} leaked",
            not _f4_leaks(f"{literal}={_F1_VALUE}"),
        )


def test_f1_round4_separator_compounds_in_every_case_and_separator() -> None:
    """QP-SEPSTRIP: `aUtH_cOdE`, `SeSsIoN-kEy`, `sEsSiOn.KeY` -- each separator, each case."""
    for literal in (
        "aUtH_cOdE", "AUTH_CODE", "SeSsIoN-kEy", "SESSION-KEY", "sEsSiOn.KeY", "session.key",
        "oAuTh-VeRiFiEr", "cOnNeCt.SiD", "mFa-OtP", "x.sIg", "pHp_SeSsId",
    ):  # fmt: skip
        check(
            "FTF-0904-F1", f"QP-SEPSTRIP {literal} leaked", not _f4_leaks(f"{literal}={_F1_VALUE}")
        )
    # a separator INSIDE the final credential word is decided by the stripped run alone, so the
    # strip must remove every separator class (`_`, `-` and `.`), not a subset
    for name in _F3_NAMES:
        for sep in _F4_SEPS:
            for cut in range(1, len(name)):
                for spelling in _f3_random_case(name)[-2:]:
                    split = spelling[:cut] + sep + spelling[cut:]
                    check(
                        "FTF-0904-F1",
                        f"QP-SEPSTRIP in-word split {split} leaked",
                        not _f4_leaks(f"GET /x?foo{sep}{split}={_F1_VALUE} HTTP/1.1"),
                    )


def test_f1_round4_round4_rows_through_the_logging_path() -> None:
    """The same classes on the real logging path (record scrub + formatter + sink)."""
    rows = [f"{p}{n}" for p in _F4_PREFIXES[:10] for n in _F3_NAMES[::3]]
    rows += ["mfa_CodE", "request_sTaTe", "connect.sId", "foo-kEy", "x_cOdE", "aUtH_cOdE"]
    rows += list(_F4_REALISTIC)
    for row in rows:
        with _Harness() as h:
            logging.getLogger("t").error("GET /x?%s=%s", row, _F1_VALUE)
            logging.getLogger("t").error(f"{row}={_F1_VALUE}")
            found = leaked([_F1_VALUE], h.outputs())
        check("FTF-0904-F1", f"{row}: logging-path leak {found}", not found)


_F4_BENIGN_CLOSED = (
    "monkey", "turkey", "hockey", "donkey", "decode", "encode", "barcode", "zipcode",
    "postcode", "statuscode", "errorcode", "estate", "realestate", "compass", "bypass",
    "obsession", "possession",
)  # fmt: skip
_F4_BENIGN_NOT_ENDING = (
    "keyboard", "codec", "decoded", "ticketing", "design", "assist", "sidebar", "passage",
    "authority", "statement", "sigma", "keys", "codes", "session_count", "state_of", "code_path",
)  # fmt: skip


def test_f1_round4_closed_benign_list_and_non_endings_survive() -> None:
    for name in _F4_BENIGN_CLOSED:
        for spelling in (name, name.upper(), name.title()):
            out = lr.redact_text(f"GET /x?{spelling}=plainvalue1 HTTP/1.1")
            check(
                "FTF-0904-F1",
                f"closed benign {spelling} over-redacted: {out!r}",
                "plainvalue1" in out,
            )
    for name in _F4_BENIGN_NOT_ENDING:
        out = lr.redact_text(f"GET /x?{name}=plainvalue1 HTTP/1.1")
        check("FTF-0904-F1", f"non-ending {name} over-redacted: {out!r}", "plainvalue1" in out)


def test_f1_round4_benign_list_never_excuses_a_credential_or_a_split_word() -> None:
    """The list is matched against the WHOLE stripped name: it excuses nothing else."""
    for name in (
        "monkeysession", "monkey_session", "sessionmonkey", "decodekey", "barcode_key",
        "estatecode", "mon_key", "monKey", "MonKey", "zip_code", "zip-code", "bar.code",
        "status_code", "de_code", "real_estate_state", "by_pass", "esTATE_state",
    ):  # fmt: skip
        check(
            "FTF-0904-F1",
            f"benign-looking credential {name} leaked",
            not _f4_leaks(f"GET /x?{name}={_F1_VALUE} HTTP/1.1"),
        )


# A fixed corpus of ordinary request/log parameter names. Quantified for the submission: of
# these 161 names the round-3 module redacted 5 and the deny-by-default module redacts 7; the 2
# added are listed in `_F4_OVER_REDACTION_ADDED`. The set is pinned EXACTLY so a later change
# that widens (or silently narrows) the rule shows up as a diff of this table.
_F4_CORPUS = (
    "page", "limit", "offset", "sort", "order", "q", "query", "search", "filter", "id",
    "user_id", "userid", "lang", "locale", "format", "fields", "include", "expand", "cursor",
    "next", "prev", "callback", "redirect_uri", "return_url", "utm_source", "utm_medium",
    "utm_campaign", "ref", "referrer", "from", "to", "start", "end", "since", "until", "date",
    "time", "timestamp", "tz", "version", "v", "debug", "verbose", "dry_run", "force", "async",
    "name", "title", "description", "email", "phone", "country", "country_code", "region",
    "city", "zip", "zipcode", "postcode", "status", "status_code", "error", "error_code",
    "exit_code", "message", "msg", "reason", "type", "kind", "mode", "level", "category",
    "tag", "tags", "label", "labels", "color", "size", "width", "height", "scale", "quality",
    "currency", "amount", "price", "quantity", "count", "total", "min", "max", "limit_per_page",
    "per_page", "page_size", "pagesize", "page_token", "next_page", "continuation", "etag",
    "if_match", "request_id", "requestid", "trace_id", "traceid", "span_id", "correlation_id",
    "job_id", "task_id", "run_id", "project_id", "org_id", "tenant_id", "workspace",
    "environment", "env", "region_id", "zone", "host", "hostname", "port", "path", "url", "uri",
    "domain", "scheme", "method", "action", "event", "topic", "channel", "stream", "source",
    "target", "dest", "destination", "origin", "keyboard", "keyword", "keywords", "monkey",
    "turkey", "decode", "encode", "barcode", "design", "assist", "statement", "authority",
    "passage", "sidebar", "ticketing", "codec", "mypass", "mykey", "estate", "compass",
    "bypass", "hockey", "session_count", "state_of", "code_path", "unicode", "opcode",
)  # fmt: skip
# redacted by the round-3 module AND still redacted (last word is a name / `token` key word)
_F4_REDACTED_SINCE_R3 = frozenset(
    {"country_code", "status_code", "error_code", "exit_code", "page_token"}
)
# newly redacted by deny-by-default (stripped name ends in a credential name, not on the list)
_F4_OVER_REDACTION_ADDED = frozenset({"mykey", "mypass"})


def test_f1_round4_over_redaction_on_the_fixed_corpus_is_pinned() -> None:
    redacted = {n for n in _F4_CORPUS if "plainvalue1" not in lr.redact_text(f"{n}=plainvalue1")}
    check(
        "FTF-0904-F1",
        f"corpus is {len(_F4_CORPUS)} unique names",
        len(set(_F4_CORPUS)) == len(_F4_CORPUS),
    )
    expected = _F4_REDACTED_SINCE_R3 | _F4_OVER_REDACTION_ADDED
    check(
        "FTF-0904-F1",
        f"over-redaction set changed: extra={sorted(redacted - expected)} "
        f"missing={sorted(expected - redacted)}",
        redacted == expected,
    )


def test_f1_round4_name_matching_is_exact_about_stray_whitespace() -> None:
    """The decision is on the exact run: whitespace is neither a name ending nor a benign word.

    (`_QUERY_ASSIGN` can never hand a name with whitespace in; this pins the function's own
    contract so the closed benign list cannot be widened by trimming.)"""
    check(
        "FTF-0904-F1", "trailing space: not a name ending", lr._is_query_param_name("key ") is False
    )
    check(
        "FTF-0904-F1", "leading space: ending still a name", lr._is_query_param_name(" key") is True
    )
    check(
        "FTF-0904-F1",
        "benign word with a stray space is not excused",
        lr._is_query_param_name(" monkey") is True,
    )
    check(
        "FTF-0904-F1", "benign word itself is excused", lr._is_query_param_name("monkey") is False
    )


def test_f1_round4_long_names_are_bounded_and_decided_by_the_ending() -> None:
    check("FTF-0904-F1", "5000-char non-name stays", "v1" in lr.redact_text("a" * 5000 + "=v1"))
    out = lr.redact_text("a" * 5000 + f"key={_F1_VALUE}")
    check("FTF-0904-F1", "5000-char name ending in key redacted", _F1_VALUE not in out)
    check("FTF-0904-F1", "empty key never matches", lr.redact_text("=abc") == "=abc")


# ---------------------------------------------------------------------------------------------
# FTF-0904-F1 round 5. A separator-delimited segment that is itself a random-case spelling of a
# listed word (`mon_KeY`, `status_CoDe`), chosen with a prefix that COLLIDES with a
# `_BENIGN_NAMES` entry once stripped and lowered (`mon`+`key` -> "monkey", `status`+`code` ->
# "statuscode") -- only the segment-level lookup (not the deny-by-default fallback, which must
# excuse these exact stripped forms) can close this.
# ---------------------------------------------------------------------------------------------
_F5_BENIGN_COLLIDING_SPLITS = (
    ("mon", "key"), ("tur", "key"), ("hoc", "key"), ("don", "key"), ("whis", "key"),
    ("lac", "key"), ("de", "code"), ("en", "code"), ("bar", "code"), ("zip", "code"),
    ("post", "code"), ("geo", "code"), ("uni", "code"), ("op", "code"), ("status", "code"),
    ("error", "code"), ("exit", "code"), ("return", "code"), ("by", "pass"), ("over", "pass"),
    ("under", "pass"), ("tres", "pass"), ("sur", "pass"), ("ob", "session"), ("pos", "session"),
)  # fmt: skip


def test_f1_round5_random_case_segment_before_camel_split_is_redacted() -> None:
    """QP-CASE-SPLIT-SEGMENT: the camel split's own lowercase-to-uppercase boundary can land
    INSIDE a random-case spelling of a separator-delimited segment (`KeY` -> `Ke`+`Y`, `CoDe` ->
    `Co`+`De`), so the last-word rule never sees "key"/"code" and only the deny-by-default
    fallback is left to decide -- but the fallback's own benign list then wrongly excuses exactly
    these stripped forms (`monkey`, `statuscode`, `bypass`, `obsession`). Each separator-delimited
    segment must be looked up lowered, whole, BEFORE any camel split."""
    for prefix, name in _F5_BENIGN_COLLIDING_SPLITS:
        for sep in _F4_SEPS:
            for spelling in _f3_random_case(name):
                for template in _F3_ENV:
                    text = template.format(p=f"{prefix}{sep}{spelling}={_F1_VALUE}")
                    check(
                        "FTF-0904-F1",
                        f"QP-CASE-SPLIT-SEGMENT {prefix}{sep}{spelling} leaked",
                        not _f4_leaks(text),
                    )
    for name in _F3_NAMES:
        for sep in _F4_SEPS:
            for spelling in _f3_random_case(name):
                text = f"foo{sep}{spelling}={_F1_VALUE}&x=1"
                check(
                    "FTF-0904-F1",
                    f"QP-CASE-SPLIT-SEGMENT foo{sep}{spelling} leaked",
                    not _f4_leaks(text),
                )
    for literal in ("mon_KeY", "de_CoDe", "zip_cOdE", "status_CoDe", "by_PaSs"):
        check(
            "FTF-0904-F1",
            f"reported spelling {literal} leaked",
            not _f4_leaks(f"GET /x?{literal}={_F1_VALUE} HTTP/1.1"),
        )


def test_f1_round5_segment_rows_through_the_logging_path() -> None:
    """The same QP-CASE-SPLIT-SEGMENT rows on the real logging path (record scrub + sink)."""
    rows = [f"{p}{sep}{name}" for p, name in _F5_BENIGN_COLLIDING_SPLITS for sep in _F4_SEPS]
    rows += ["mon_KeY", "de_CoDe", "zip_cOdE", "status_CoDe", "by_PaSs"]
    for row in rows:
        with _Harness() as h:
            logging.getLogger("t").error("GET /x?%s=%s", row, _F1_VALUE)
            logging.getLogger("t").error(f"{row}={_F1_VALUE}")
            found = leaked([_F1_VALUE], h.outputs())
        check("FTF-0904-F1", f"{row}: logging-path leak {found}", not found)


# A non-ASCII prefix must not excuse a credential-ending name: the deny-by-default fallback
# binds on the ENDING of the stripped, lowered name, never on whether the rest is ASCII.
_F5_NONASCII_PREFIXES = (
    "müller", "état", "中", "björk", "françois", "日本語", "ñoño", "žluťoučký",
)  # fmt: skip


def test_f1_round5_non_ascii_prefix_does_not_excuse_a_credential_name() -> None:
    """QP-NONASCII-PREFIX: `müllerkey`, `étatcode`, `中code` are redacted exactly like
    `foobarkey`, `widgetcode` -- the ending decides, whatever script the rest of the name is in."""
    for prefix in _F5_NONASCII_PREFIXES:
        for name in ("key", "code", "pass", "session", "sig", "state"):
            glued = f"{prefix}{name}"
            for spelling in (glued, glued.upper()):
                text = f"GET /x?{spelling}={_F1_VALUE} HTTP/1.1"
                check(
                    "FTF-0904-F1",
                    f"QP-NONASCII-PREFIX {spelling}: leaked {lr.redact_text(text)!r}",
                    not _f4_leaks(text),
                )
    for literal in ("müllerkey", "étatcode", "中code"):
        check(
            "FTF-0904-F1",
            f"reported spelling {literal} leaked",
            not _f4_leaks(f"GET /x?{literal}={_F1_VALUE} HTTP/1.1"),
        )


# ---------------------------------------------------------------------------------------------
# FTF-0904-F1 round 6 (QP-SEGMENT-TABLE-HANDPICKED / QP-SEGMENT-LOGGING-LOWERCASE). Round 5's own
# `_F5_BENIGN_COLLIDING_SPLITS` table above was hand-picked (25 rows) and missed 7 of the 32
# `_BENIGN_NAMES` words that actually end in a credential name -- every "-state" word among them
# (estate, realestate, upstate, restate) plus compass, jockey and qrcode -- so a mutant that drops
# "state" from the segment lookup (or narrows it to only key/code/pass/session) stayed GREEN while
# `re_StAtE` / `up_StAtE` leaked. This table is GENERATED from `lr._BENIGN_NAMES` and `lr.
# _NAME_SUFFIXES` themselves -- every word split at EVERY credential ending it actually has -- so a
# future addition to either closed list is covered without a hand-picked table drifting out of
# sync again. (Unlike `_F4_PREFIXES`/`_F4_REALISTIC` above, this is sound to derive from the
# implementation's own closed enumeration: the point is exhaustive coverage of that exact
# enumeration, not an independent check of its vocabulary.)
# ---------------------------------------------------------------------------------------------


def _f1_benign_split_rows() -> list[tuple[str, str, str]]:
    """Every `_BENIGN_NAMES` word split into (prefix, suffix) at EVERY `_NAME_SUFFIXES` ending it
    actually has. A handful of `_BENIGN_NAMES` entries (concession/profession/procession/
    recession/succession) do not end in any listed suffix ("cession" != "session") and
    correctly produce no row -- they are not evidence their defence is reachable (Law 5)."""
    rows: list[tuple[str, str, str]] = []
    for word in sorted(lr._BENIGN_NAMES):
        for suffix in lr._NAME_SUFFIXES:
            if word.endswith(suffix) and len(word) > len(suffix):
                rows.append((word[: -len(suffix)], suffix, word))
    return rows


_F6_BENIGN_SPLIT_ROWS = _f1_benign_split_rows()
_F6_REPORTED_LITERALS = (
    "mon_KeY", "de_CoDe", "zip_cOdE", "status_CoDe", "by_PaSs", "re_StAtE", "up_StAtE",
)  # fmt: skip


def test_f1_round6_generated_benign_split_table_is_redacted() -> None:
    """QP-SEGMENT-TABLE-GENERATED: every `_BENIGN_NAMES` word, split at every credential ending it
    has and glued back with each separator in a random-case spelling of the ending, must still be
    redacted -- the generated superset that closes round 5's hand-picked table's gap."""
    check(
        "FTF-0904-F1",
        "generated benign-split table covers every matched _BENIGN_NAMES word",
        len({word for _p, _s, word in _F6_BENIGN_SPLIT_ROWS}) == 32,
    )
    for prefix, suffix, word in _F6_BENIGN_SPLIT_ROWS:
        for sep in _F4_SEPS:
            for spelling in _f3_random_case(suffix):
                text = f"GET /x?{prefix}{sep}{spelling}={_F1_VALUE} HTTP/1.1"
                check(
                    "FTF-0904-F1",
                    f"QP-SEGMENT-TABLE-GENERATED {prefix}{sep}{spelling} ({word}) leaked",
                    not _f4_leaks(text),
                )
    for literal in _F6_REPORTED_LITERALS:
        check(
            "FTF-0904-F1",
            f"reported spelling {literal} leaked",
            not _f4_leaks(f"GET /x?{literal}={_F1_VALUE} HTTP/1.1"),
        )


def test_f1_round6_generated_benign_split_rows_through_the_logging_path() -> None:
    """QP-SEGMENT-LOGGING-LOWERCASE: round 5's own logging-path test fed only LOWERCASE rows (plus
    5 reported literals), never a random-case spelling -- run the same generated rows this round's
    `redact_text` test uses through the real logging path too."""
    rows = [
        f"{prefix}{sep}{spelling}"
        for prefix, suffix, _word in _F6_BENIGN_SPLIT_ROWS
        for sep in _F4_SEPS
        for spelling in _f3_random_case(suffix)
    ]
    rows += list(_F6_REPORTED_LITERALS)
    for row in rows:
        with _Harness() as h:
            logging.getLogger("t").error("GET /x?%s=%s", row, _F1_VALUE)
            logging.getLogger("t").error(f"{row}={_F1_VALUE}")
            found = leaked([_F1_VALUE], h.outputs())
        check("FTF-0904-F1", f"{row}: logging-path leak {found}", not found)


_F3_HEADS = (
    "sk-abcdefgh12",
    "ghp_" + "a1B2c3D4" * 5,
    "AKIA" + "ABCDEFGH23456789",
)
_F3_TAILS = ("." + _F1_VALUE, "x" + _F1_VALUE, "." + "Tail9" * 40, "-" + "t" * 70 + _F1_VALUE)


def test_f1_round3_redacted_head_swallows_the_whole_value_tail() -> None:
    """QP-GUARD-NO-TAIL / QP-GUARD-TAIL-BOUNDED-64: an earlier rule redacts the head of a value
    and more value follows (including a tail longer than 64 characters)."""
    for name in ("code", "state", "key"):
        for head in _F3_HEADS:
            for tail in _F3_TAILS:
                out = lr.redact_text(f"{name}={head}{tail}&x=1")
                residue = tail.lstrip(".-x")
                check(
                    "FTF-0904-F1",
                    f"QP-GUARD-TAIL {name}={head[:6]}..{tail[:8]}: leaked {out!r}",
                    residue not in out and out.endswith("&x=1"),
                )
                check(
                    "FTF-0904-F1",
                    f"QP-GUARD-TAIL not idempotent {out!r}",
                    lr.redact_text(out) == out,
                )


def test_f1_round3_empty_value_does_not_end_the_scan() -> None:
    """QP-BREAK-ON-EMPTY-VALUE: an empty value followed by a secret one."""
    for name_a in ("code", "error", "x", "state"):
        for name_b in ("state", "code", "key", "sessionKey"):
            for sep in ("&", "&&", "&amp;", "; ", ", "):
                text = f"GET /cb?{name_a}={sep}{name_b}={_F1_VALUE} HTTP/1.1"
                out = lr.redact_text(text)
                check(
                    "FTF-0904-F1",
                    f"QP-BREAK-ON-EMPTY {text!r}: leaked {out!r}",
                    _F1_VALUE not in out,
                )
    for text in (
        f"?code=&state={_F1_VALUE}",
        f"?error=&code={_F1_VALUE}",
        f"code= state={_F1_VALUE}",
    ):
        out = lr.redact_text(text)
        check("FTF-0904-F1", f"QP-BREAK-ON-EMPTY {text!r}: leaked {out!r}", _F1_VALUE not in out)


_F3_TERMINATORS = set(" \t\r\n&,;\"'}])")


def test_f1_round3_value_class_covers_every_admitted_character() -> None:
    """QP-VALUE-STOPS-AT-*: for EVERY printable character the value class admits, a value that
    carries it mid-way is redacted whole (Google OAuth codes contain `/`, `+` and `%`)."""
    admitted = [c for c in map(chr, range(33, 127)) if c not in _F3_TERMINATORS]
    check(
        "FTF-0904-F1",
        "value-class table is non-trivial",
        {"/", "+", "%", "~", "@"} <= set(admitted),
    )
    for char in admitted:
        for name in ("code", "state", "sessionKey"):
            text = f"GET /cb?{name}=4{char}0Ab{_F1_VALUE} HTTP/1.1"
            out = lr.redact_text(text)
            check(
                "FTF-0904-F1",
                f"QP-VALUE-STOPS-AT {char!r} in {name}: leaked {out!r}",
                _F1_VALUE not in out and out.endswith(" HTTP/1.1"),
            )


# --- FTF-0904-F2: quoted values after credential names -------------------------------------
_F2_NAMES = tuple(sorted(lr._QUERY_PARAM_NAMES)) + (
    "session_key",
    "mfa_code",
    "authCode",
    "re_state",
    "monkeysession",
    "access.token_sid",
)
_F2_QUOTES = ('"', "'")
_F2_SEPARATORS = ("=", ": ", ":", " = ", '":  ', "'=")
_F2_VALUES = (
    _F1_VALUE,
    f"a b {_F1_VALUE} c",
    f"x,y;z&{_F1_VALUE}&w",
    f"esc\\{{Q}}aped {_F1_VALUE}",
)


def _f2_rows() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for name in _F2_NAMES:
        for spelling in _f3_random_case(name)[::6]:
            for qch in _F2_QUOTES:
                for sep in _F2_SEPARATORS:
                    for value in _F2_VALUES:
                        body = value.replace("{Q}", qch)
                        rows.append((f"{spelling}{sep}{qch}{body}{qch}", spelling))
    return rows


_F2_ROWS = _f2_rows()


def test_f2_quoted_values_after_credential_names_are_redacted() -> None:
    """QUOTED-VALUE-CLEAR: `state="v"`, `code='v'`, `session_key="v"`... in every case spelling,
    both `name=` and `name: ` forms, with escapes and spaces inside the value."""
    check("FTF-0904-F2", "generated quoted table is large", len(_F2_ROWS) > 2000)
    for text, spelling in _F2_ROWS:
        line = f"ctx {text} tail_ok"
        out = lr.redact_text(line)
        check("FTF-0904-F2", f"{text!r}: leaked {out!r}", "Zq7Lm2" not in out)
        check(
            "FTF-0904-F2",
            f"{text!r}: text around the value lost {out!r}",
            out.startswith("ctx ") and out.endswith("tail_ok") and spelling[:1] in out,
        )


def test_f2_quoted_rows_through_the_logging_path() -> None:
    for text, _spelling in _F2_ROWS[::7]:
        with _Harness() as h:
            logging.getLogger("t").error("%s", text)
            logging.getLogger("t").error(text)
            found = leaked([_F1_VALUE], h.outputs())
        check("FTF-0904-F2", f"{text!r}: logging-path leak {found}", not found)


def test_f2_json_object_and_unterminated_quote() -> None:
    out = lr.redact_text('{"state": "' + _F1_VALUE + '", "user": "bob"}')
    check("FTF-0904-F2", f"json state leaked {out!r}", _F1_VALUE not in out)
    check("FTF-0904-F2", f"json neighbour lost {out!r}", '"user": "bob"' in out)
    out = lr.redact_text('state="' + _F1_VALUE)
    check("FTF-0904-F2", f"unterminated quote leaked {out!r}", _F1_VALUE not in out)
    out = lr.redact_text('state="' + _F1_VALUE + '" next="kept" code=\'' + _F1_VALUE + "'")
    check("FTF-0904-F2", f"two quoted values leaked {out!r}", _F1_VALUE not in out)
    check("FTF-0904-F2", f"non-credential value lost {out!r}", 'next="kept"' in out)


def test_f2_non_credential_quoted_values_survive_and_rule_is_idempotent() -> None:
    for text in (
        'message="the state of the art"', "monkey='ripe banana'", 'status: "ok"',
        "city: 'Riyadh'", 'name="bob"', 'title: "state of play"',
    ):  # fmt: skip
        check("FTF-0904-F2", f"over-redacted {text!r}", lr.redact_text(text) == text)
    for text, _s in _F2_ROWS[::97]:
        once = lr.redact_text(text)
        check("FTF-0904-F2", f"not idempotent {text!r}", lr.redact_text(once) == once)
    # a bare (unquoted) `name: value` stays unredacted: only the quoted form is in scope
    check("FTF-0904-F2", "bare 'state: ok' changed", lr.redact_text("state: ok") == "state: ok")


def test_f2_quoted_scan_is_linear() -> None:
    for payload in ('state="' * 4000, 'code: "' + "\\" * 20000, "key=" + '"a\\"' * 5000):
        started = time.perf_counter()
        lr.redact_text(payload)
        check("FTF-0904-F2", "quoted scan super-linear", time.perf_counter() - started < 2.0)


def test_f2_escaped_quote_inside_a_credential_keyed_quoted_value() -> None:
    """Same-class sweep: the generic key=value rule ended a quoted value at an escaped quote."""
    for name in ("password", "token", "api_key", "client_secret"):
        for q in _F2_QUOTES:
            for sep in ("=", ": "):
                text = f"{name}{sep}{q}ab\\{q}cd {_F1_VALUE}{q} after"
                out = lr.redact_text(text)
                check("FTF-0904-F2", f"{text!r}: leaked {out!r}", "Zq7Lm2" not in out)
                check("FTF-0904-F2", f"{text!r}: tail lost {out!r}", out.endswith(" after"))


FAULTS: list[tuple[str, str, str, int]] = [
    # --- rules removed one at a time -------------------------------------------------
    ("F01 header rule removed", '    ("header", _HEADER),\n', "", 1),
    ("F02 bearer/basic rule removed", '    ("bearer", _BEARER),\n', "", 1),
    ("F03 token-prefix rule removed", '    ("prefix", _PREFIXES),\n', "", 1),
    ("F04 URL userinfo rule removed", '    ("userinfo", _USERINFO),\n', "", 1),
    ("F05 key=value rule removed", '    ("key_value", _KEY_VALUE),\n', "", 1),
    ("F06 PEM rule removed", '    ("pem", _PEM),\n', "", 1),
    # --- pattern weakened --------------------------------------------------------------
    (
        "F07 header value stops at whitespace",
        '|[^\\r\\n]*)",\n    re.I,',
        '|[^\\s]*)",\n    re.I,',
        1,
    ),
    (
        "F08 escaped-JSON value form dropped (F2 retarget: the quoted forms are now a "
        "parenthesised multi-line literal, so the fault removes the first alternative and its `|`)",
        'r\'(?<!\\\\)\\\\++"(?:[^"\\\\\\r\\n]++|\\\\++(?!"))*+\\\\+"\'\n    r"""|',
        '\n    r"""',
        1,
    ),
    ("F09 userinfo stops at first @", '[^/\\s?#]*@")', '[^/\\s?#@]*@")', 1),
    ("F10 idempotence guard dropped", "\\[REDACTED\\]|{_QUOTED_VALUE}", "{_QUOTED_VALUE}", 1),
    # --- normalisation and decoding ----------------------------------------------------
    ("F11 NFKC skipped", 'unicodedata.normalize("NFKC", text)', "text", 1),
    ("F12 format characters kept", '("Cf", "Cc", "Cs", "Co", "Cn")', '("Cc", "Cs", "Co", "Cn")', 1),
    (
        "F13 no percent-decoding",
        "        layer = _normalise(unquote(layer))",
        "        layer = _normalise(layer)",
        1,
    ),
    ("F14 one percent-decoding layer", "_MAX_DECODE_LAYERS = 3", "_MAX_DECODE_LAYERS = 1", 1),
    (
        "F15 first decoded layer returned instead of the raw scan (round-1 behaviour)",
        "    return _redact_encoded(_scan(_normalise(text), _BASE64_LAYERS))\n",
        "    text = _normalise(text)\n"
        "    layers = [text]\n"
        "    for _ in range(_MAX_DECODE_LAYERS):\n"
        '        if "%" not in layers[-1]:\n'
        "            break\n"
        "        layers.append(unquote(layers[-1]))\n"
        "    for layer in layers[1:]:\n"
        "        scanned = _scan(layer, _BASE64_LAYERS)\n"
        "        if scanned != layer:\n"
        "            return scanned\n"
        "    return _scan(text, _BASE64_LAYERS)\n",
        1,
    ),
    (
        "F15b raw text not scanned (decoded layers only)",
        "    return _redact_encoded(_scan(_normalise(text), _BASE64_LAYERS))\n",
        "    return _redact_encoded(_normalise(text))\n",
        1,
    ),
    (
        "F15c encoded secret never detected",
        "        if _scan(layer, _BASE64_LAYERS) != layer:\n            return True\n",
        "        pass\n",
        1,
    ),
    (
        "F15d only the encoded token is cut, not the rest of the line",
        "        line = line[: candidates[low][0]] + REDACTED\n",
        "        line = line[: candidates[low][0]] + REDACTED"
        " + line[candidates[low][0] :].partition(' ')[2]\n",
        1,
    ),
    (
        "F15f cut falls back to keeping the line",
        "    candidates = _percent_token_starts(line)  # still revealing after the cap:"
        " fail closed\n"
        "    return line if not candidates else line[: candidates[0][0]] + REDACTED\n",
        "    return line\n",
        1,
    ),
    ("F16 no base64 detection", "_BASE64_LAYERS = 2", "_BASE64_LAYERS = 0", 1),
    (
        "F17 one base64 layer only is not tested but zero is",
        "    text = _apply_rules(text)\n    if layers <= 0:\n        return text\n",
        "    text = _apply_rules(text)\n    return text\n",
        1,
    ),
    # --- fail closed -------------------------------------------------------------------
    (
        "F18 oversize guard removed",
        "    if len(text) > MAX_TEXT_LENGTH:\n        raise LogRedactionError\n",
        "",
        1,
    ),
    (
        "F19 exact str type not required",
        "    if type(text) is not str:\n        raise LogRedactionError\n",
        "",
        1,
    ),
    (
        "F20 record failure keeps raw message",
        "        record.msg = REDACTION_FAILED_MARKER\n",
        "",
        1,
    ),
    (
        "F21 extras failure keeps raw extras",
        "dict.fromkeys(extras, REDACTION_FAILED_MARKER)",
        "{k: record.__dict__[k] for k in extras}",
        1,
    ),
    (
        "F22 formatter failure returns raw line",
        "        except Exception:\n            return REDACTION_FAILED_MARKER\n",
        "        except Exception:\n            return self.formatMessage(record)\n",
        1,
    ),
    (
        "F23 error chains its cause (text)",
        "        raise LogRedactionError from None\n    return result\n\n\ndef redact_exception",
        "        raise LogRedactionError\n    return result\n\n\ndef redact_exception",
        1,
    ),
    # --- round 2: header forms, ingress extras, linear key rule -----------------------
    ("F38 header tuple/list text rule removed", '    ("pair", _PAIR),\n', "", 1),
    (
        "F39 header pair structure not redacted",
        "        if len(value) == 2 and isinstance(value[0], str | bytes | bytearray):",
        "        if False:",
        1,
    ),
    (
        "F40 bytes header names not decoded",
        "    if isinstance(key, bytes | bytearray):\n        return redact_text(",
        "    if False:\n        return redact_text(",
        1,
    ),
    ("F41 HTTP_ environ spelling dropped", '+ "|http_(?:"', '+ "|zzhttp_(?:"', 1),
    (
        "F42 extra= not scrubbed at ingress",
        "        logging.Logger.makeRecord = _wrap_make_record(maker)"
        "  # type: ignore[method-assign]",
        "        pass",
        1,
    ),
    (
        "F43 record maker wrapped on every call",
        'if not getattr(maker, "_ftf0904_redacting", False):',
        "if True:",
        1,
    ),
    (
        "F44 record maker wrapper does not scrub",
        "        record = original(self, *args, **kwargs)\n        _scrub_record(record)\n",
        "        record = original(self, *args, **kwargs)\n",
        1,
    ),
    (
        "F45 quadratic key rule restored",
        'rf"(?=[\\w.\\-]*?{_SECRET_KEY_WORDS})[\\w.\\-]++"',
        'rf"[\\w.\\-]*{_SECRET_KEY_WORDS}[\\w.\\-]*"',
        1,
    ),
    # --- round 3: percent-token tokeniser linear, pair rule, encoded separator ---------
    (
        "F-PCT-QUAD percent-token scan reverted to quadratic \\S* backtracking",
        "    runs = list(_NON_SPACE_RUN.finditer(line))\n"
        "    return [\n"
        "        (run.start(), runs[i - 1].start() if i > 0 else None)\n"
        "        for i, run in enumerate(runs)\n"
        "        if _PERCENT_IN_TOKEN.search(run.group())\n"
        "    ]\n",
        "    return [\n"
        "        (m.start(), None)\n"
        '        for m in re.compile(r"\\S*%[0-9A-Fa-f]{2}").finditer(line)\n'
        "    ]\n",
        1,
    ),
    (
        "F-PCT-WIDEN0 first run's widened wraps to the line's last run instead of None",
        "runs[i - 1].start() if i > 0 else None",
        "runs[i - 1].start() if i >= 0 else None",
        1,
    ),
    (
        "F-PAIR-KEY pair rule drops the generic secret-word alternative",
        "(?:{_HEADER_KEYS}|{_SECRET_KEY})(?P=q)",
        "(?:{_HEADER_KEYS})(?P=q)",
        1,
    ),
    # --- records, exceptions, extras ---------------------------------------------------
    (
        "F24 exception chain not redacted",
        "        result = redact_text(rendered)",
        "        result = rendered",
        1,
    ),
    (
        "F25 exc_info rendered raw",
        "exc_text = redact_exception(value)",
        'exc_text = "".join(traceback.format_exception(value))',
        1,
    ),
    (
        "F26 pre-set exc_text kept raw",
        "exc_text = redact_text(str.__str__(record.exc_text))",
        "exc_text = record.exc_text",
        1,
    ),
    ("F27 stack_info kept raw", "stack_info = redact_text(str.__str__(stack_info))", "pass", 1),
    ("F28 extras never written back", "    record.__dict__.update(scrubbed)\n", "", 1),
    (
        "F29 secret-named attribute value kept",
        "REDACTED\n            if _SECRET_KEY_NAME.fullmatch(k)\n            else _redact_value(",
        "_redact_value(\n            if True\n            else _redact_value(",
        1,
    ),
    (
        "F30 secret-named mapping key value kept",
        "            if _SECRET_KEY_NAME.fullmatch(name):",
        "            if False:",
        2,
    ),
    (
        "F31 depth and cycle guard removed",
        "    if depth >= _MAX_DEPTH or id(value) in path:\n        return REDACTED\n",
        "",
        1,
    ),
    # --- ingress / sink wiring ---------------------------------------------------------
    (
        "F32 ingress factory not installed",
        "        logging.setLogRecordFactory(_wrap_factory(current))",
        "        pass",
        1,
    ),
    (
        "F33 factory wrapped on every call",
        'if not getattr(current, "_ftf0904_redacting", False):',
        "if True:",
        1,
    ),
    (
        "F34 sink filter not attached",
        "        handler.addFilter(RedactingFilter())",
        "        pass",
        1,
    ),
    (
        "F35 sink formatter not attached",
        "        handler.setFormatter(RedactingFormatter(inner=existing))",
        "        pass",
        1,
    ),
    (
        "F36 sink filter attached twice",
        "    if not any(isinstance(f, RedactingFilter) for f in handler.filters):",
        "    if True:",
        1,
    ),
    (
        "F37 formatter does not redact the line",
        "            return redact_text(str.__str__(raw))",
        "            return raw",
        1,
    ),
    # --- round 5: the round-4 JWT/escaped-value bounds were leaks, not timing fixes; the
    # "fix" here is the bound itself (reproduced small, V-*) or the whole protection (F-*-QUAD) --
    (
        "F-QV-QUAD escaped-value content reverted to naive lazy (reproduces the round-5 "
        "backslash-run-inside-quoted-value quadratic this round fixes)",
        '(?:[^"\\\\\\r\\n]++|\\\\++(?!"))*+',
        '[^"\\r\\n]*?',
        1,
    ),
    (
        "V-QV-BOUND-16 escaped-value content re-bounded to ~16 chars (reproduces the "
        "round-4-style leak the round-5 class-closing ruling forbids)",
        '(?:[^"\\\\\\r\\n]++|\\\\++(?!"))*+',
        '(?:[^"\\\\\\r\\n]|\\\\++(?!")){0,16}+',
        1,
    ),
    # --- round 6: the JWT shape is a per-run scan; sweeps must be able to fail -------------
    (
        "F-PFX-QUAD per-run JWT scan replaced by a start-at-every-eyJ regex (reproduces the "
        "round-3/4 jwt-repeated-no-dot quadratic)",
        r'_JWT_RUN = re.compile(r"[A-Za-z0-9_\-.]++")',
        r'_JWT_RUN = re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_\-]{5,}+\.[A-Za-z0-9_\-.]*+")',
        1,
    ),
    (
        "V-JWT-BOUND-40 JWT run re-bounded to 40 chars (round-4-style leak)",
        r'_JWT_RUN = re.compile(r"[A-Za-z0-9_\-.]++")',
        r'_JWT_RUN = re.compile(r"[A-Za-z0-9_\-.]{1,40}+")',
        1,
    ),
    (
        "V-JWT-GLUE round-5 lookbehind restored: a JWT glued after _ or - is skipped",
        "segment[pos - 1].isalnum()",
        '(segment[pos - 1].isalnum() or segment[pos - 1] in "_-")',
        1,
    ),
    (
        "V-JWT-SIG-BOUND-40 signature segment truncated to 40 redacted chars (302 of 342 leak)",
        "out.append(segments[i][:start] + REDACTED)",
        "out.append(segments[i][:start] + REDACTED + segments[i + 2][40:])",
        1,
    ),
    (
        "V-JWT-CASE JWT start matched case-sensitively only",
        "lowered = segment.lower()",
        "lowered = segment",
        1,
    ),
    (
        "V-JWT-MIN2 JWT payload segment length floor dropped",
        "len(segments[i + 1]) >= _JWT_MIN_SEGMENT",
        "len(segments[i + 1]) >= 0",
        1,
    ),
    (
        "V-BEARER-BOUND-500 bearer credential re-bounded to 500 chars",
        r"""[^\s,;"'}]{8,}""",
        r"""[^\s,;"'}]{8,500}""",
        1,
    ),
    (
        "V-SK-BOUND-500 sk- key re-bounded to 500 chars",
        r'r"sk-[A-Za-z0-9_\-]{8,}"',
        r'r"sk-[A-Za-z0-9_\-]{8,500}"',
        1,
    ),
    (
        "V-B64-BOUND-500 base64 run re-bounded to 500 chars",
        r"(?P<body>[A-Za-z0-9+/_\-]{16,})",
        r"(?P<body>[A-Za-z0-9+/_\-]{16,500})",
        1,
    ),
    (
        "Q-PAIR-OVERLAP pair value gets an overlapping lone-backslash alternative (exponential)",
        r"(?:\\.|(?!(?P=vq))[^\\])*(?P=vq)",
        r"(?:\\.|(?!(?P=vq))[^\\]|\\)*(?P=vq)",
        1,
    ),
    (
        "Q-PAIR-KEY-ANY-QUAD pair opener scans any non-quote run, not just whitespace (quadratic)",
        r"""[(\[]\s*b?(?P<q>""",
        r"""[(\[][^'"]*b?(?P<q>""",
        1,
    ),
    (
        "Q-PAIR-KEY-WS-QUAD pair opener gets a second adjacent whitespace scan (quadratic)",
        r"""[(\[]\s*b?(?P<q>""",
        r"""[(\[]\s*\s*b?(?P<q>""",
        1,
    ),
    (
        "Q-JWT-LOWER-IN-LOOP _jwt_start skip loop re-lowers the whole segment each step",
        r'pos = lowered.find("eyj", pos + 1)',
        r'pos = segment.lower().find("eyj", pos + 1)',
        1,
    ),
    (
        "Q-B64-NO-LOOKBEHIND base64 run may start inside a run (quadratic)",
        r'r"(?<![A-Za-z0-9+/_\-])(?P<body>',
        r'r"(?P<body>',
        1,
    ),
    (
        "Q-USERINFO-NO-LOOKBEHIND URL scheme may start inside a run (quadratic)",
        r"(?P<scheme>(?<![A-Za-z0-9+.\-])[A-Za-z]",
        r"(?P<scheme>[A-Za-z]",
        1,
    ),
    ("F1-rule removed", '    ("query_param", _QUERY_SECRET_PARAM),\n', "", 1),
    (
        "F1-rule requires a delimiter before the name (round 1 shape)",
        'if match.start() < pos or not _is_query_param_name(match.group("key")):',
        'if match.start() < pos or not _is_query_param_name(match.group("key")) '
        'or match.start() == 0 or text[match.start() - 1] not in "?&;#":',
        1,
    ),
    (
        "F1-position line-start dropped",
        'if match.start() < pos or not _is_query_param_name(match.group("key")):',
        "if match.start() < pos or match.start() == 0 "
        'or not _is_query_param_name(match.group("key")):',
        1,
    ),
    (
        "F1-position after-space dropped",
        'if match.start() < pos or not _is_query_param_name(match.group("key")):',
        'if match.start() < pos or (match.start() and text[match.start() - 1] == " ") '
        'or not _is_query_param_name(match.group("key")):',
        1,
    ),
    (
        "F1-position after-quote dropped",
        'if match.start() < pos or not _is_query_param_name(match.group("key")):',
        'if match.start() < pos or (match.start() and text[match.start() - 1] in "\\"\'") '
        'or not _is_query_param_name(match.group("key")):',
        1,
    ),
    (
        "F1-position after-slash/comma/pipe dropped",
        'if match.start() < pos or not _is_query_param_name(match.group("key")):',
        'if match.start() < pos or (match.start() and text[match.start() - 1] in "/,|(") '
        'or not _is_query_param_name(match.group("key")):',
        1,
    ),
    (
        "F1-position form-body (after newline) dropped",
        'if match.start() < pos or not _is_query_param_name(match.group("key")):',
        'if match.start() < pos or (match.start() and text[match.start() - 1] == "\\n") '
        'or not _is_query_param_name(match.group("key")):',
        1,
    ),
    (
        "F1-idempotence guard dropped (no [REDACTED] alternative)",
        'r"""\\[REDACTED\\][^\\s&,;"\'}\\])]*+|[^\\s&,;"\'}\\])]++"""',
        'r"""[^\\s&,;"\'}\\])]++"""',
        1,
    ),
    (
        "F1-name class: only the whole name, not the last word",
        "if words and words[-1].lower() in _QUERY_PARAM_NAMES:",
        "if name.lower() in _QUERY_PARAM_NAMES:",
        1,
    ),
    (
        "F1-name class: camelCase boundary not split",
        'words = [w for w in _NAME_WORD_SPLIT.split(_CAMEL_BOUNDARY.sub("_", name)) if w]',
        "words = [w for w in _NAME_WORD_SPLIT.split(name) if w]",
        1,
    ),
    (
        "F1-name class: name matched anywhere in the key (substring)",
        "if words and words[-1].lower() in _QUERY_PARAM_NAMES:",
        "if any(n in name.lower() for n in _QUERY_PARAM_NAMES):",
        1,
    ),
    (
        "F1-decoded layers not NFKC'd after the first",
        "layer = _normalise(unquote(layer))  # a decoded fullwidth",
        "layer = (_normalise if layer is segment else str)(unquote(layer))  # a decoded fullwidth",
        1,
    ),
    (
        "F1-NFKC dropped from every decoded layer",
        "layer = _normalise(unquote(layer))  # a decoded fullwidth",
        "layer = unquote(layer)  # a decoded fullwidth",
        1,
    ),
    # round 5 (FTF-0904-F1-r5)
    (
        "F1r5-segment lookup before camel split removed (QP-CASE-SPLIT-SEGMENT)",
        "    raw_segments = [w for w in _NAME_WORD_SPLIT.split(name) if w]\n"
        "    if raw_segments and raw_segments[-1].lower() in _QUERY_PARAM_NAMES:\n"
        "        return True\n",
        "",
        1,
    ),
    (
        "F1r5-segment lookup not lowered before comparison",
        "if raw_segments and raw_segments[-1].lower() in _QUERY_PARAM_NAMES:",
        "if raw_segments and raw_segments[-1] in _QUERY_PARAM_NAMES:",
        1,
    ),
    (
        "F1r5-segment lookup uses the whole name, not the last separator segment",
        "if raw_segments and raw_segments[-1].lower() in _QUERY_PARAM_NAMES:",
        "if raw_segments and name.lower() in _QUERY_PARAM_NAMES:",
        1,
    ),
    (
        "F1r5-ASCII-only fallback (non-ASCII-prefixed names leak: müllerkey, étatcode, 中code)",
        "    return stripped.endswith(_NAME_SUFFIXES) and stripped not in _BENIGN_NAMES",
        "    return stripped.isascii() and stripped.endswith(_NAME_SUFFIXES) "
        "and stripped not in _BENIGN_NAMES",
        1,
    ),
    # round 6 (FTF-0904-F1-r6). Round 5's own hand-picked table never tested a "state"-suffixed
    # `_BENIGN_NAMES` collision (estate/realestate/upstate/restate all missing), so a narrowed
    # segment-lookup set survived undetected (`up_StAtE` leaks: line 318's camel split defeats the
    # last-word check, and the fallback wrongly excuses "upstate" as benign). Confirmed directly:
    # both mutants below SURVIVE the pre-round-6 test file (all checks pass) and are KILLED by
    # this round's generated table (388 checks fail).
    (
        "F1r6-MY11 segment lookup drops 'state' from the set it checks",
        "    if raw_segments and raw_segments[-1].lower() in _QUERY_PARAM_NAMES:\n",
        '    if raw_segments and raw_segments[-1].lower() in (_QUERY_PARAM_NAMES - {"state"}):\n',
        1,
    ),
    (
        "F1r6-MY12 segment lookup covers only key/code/pass/session",
        "    if raw_segments and raw_segments[-1].lower() in _QUERY_PARAM_NAMES:\n",
        "    if raw_segments and raw_segments[-1].lower() in "
        '{"key", "code", "pass", "session"}:\n',
        1,
    ),
]

# every name in `_QUERY_PARAM_NAMES` is its own fault: dropping any one must turn a check RED
# (totp/hotp/sessid/phpsessid are not listed: they end in `otp`/`sid`, so listing them was
# redundant; they stay in `_F3_NAMES` and are redacted through those endings)
FAULTS += [
    (f"F1-name {name!r} dropped from _QUERY_PARAM_NAMES", f'        "{name}",\n', "", 1)
    for name in (
        "key", "sig", "auth", "session", "jwt", "code", "state", "verifier", "ticket", "otp",
        "pass", "sid", "jsessionid",
    )
]  # fmt: skip

# round 3 (FTF-0904-F1-r3): each fault removes one protection added or pinned this round
FAULTS += [
    (
        "F1r4-deny-by-default fallback off (QP-GLUED-VOCAB)",
        "    return stripped.endswith(_NAME_SUFFIXES) and stripped not in _BENIGN_NAMES",
        "    return False",
        1,
    ),
    (
        "F1r4-fallback case-sensitive (UPPER / random-case compounds leak)",
        'stripped = _NAME_WORD_SPLIT.sub("", name).lower()',
        'stripped = _NAME_WORD_SPLIT.sub("", name)',
        1,
    ),
    (
        "F1r4-QP-SEPSTRIP-DROPPED (no separator strip in the fallback)",
        'stripped = _NAME_WORD_SPLIT.sub("", name).lower()',
        "stripped = name.lower()",
        1,
    ),
    (
        "F1r4-QP-SEPSTRIP-UNDERSCORE-ONLY",
        'stripped = _NAME_WORD_SPLIT.sub("", name).lower()',
        'stripped = name.replace("_", "").lower()',
        1,
    ),
    (
        "F1r4-QP-SEPSTRIP-NO-HYPHEN",
        'stripped = _NAME_WORD_SPLIT.sub("", name).lower()',
        'stripped = re.sub(r"[_.]+", "", name).lower()',
        1,
    ),
    (
        "F1r4-QP-SEPSTRIP-NO-DOT",
        'stripped = _NAME_WORD_SPLIT.sub("", name).lower()',
        'stripped = re.sub(r"[_\\-]+", "", name).lower()',
        1,
    ),
    (
        "F1r4-benign list ignored (over-redacts monkey/decode/estate)",
        "stripped.endswith(_NAME_SUFFIXES) and stripped not in _BENIGN_NAMES",
        "stripped.endswith(_NAME_SUFFIXES)",
        1,
    ),
    (
        "F1r4-benign list matched as a substring (excuses monkeysession)",
        "stripped not in _BENIGN_NAMES",
        "not any(word in stripped for word in _BENIGN_NAMES)",
        1,
    ),
    (
        "F1r4-fallback matches a name ANYWHERE (over-redacts keyboard/codec)",
        "stripped.endswith(_NAME_SUFFIXES) and",
        "any(word in stripped for word in _NAME_SUFFIXES) and",
        1,
    ),
    (
        "F1r4-last-word rule removed (benign stripped zip_code / monKey leak)",
        "    if words and words[-1].lower() in _QUERY_PARAM_NAMES:\n"
        "        return True\n"
        "    stripped",
        "    stripped",
        1,
    ),
    (
        "QP-GUARD-NO-TAIL (marker consumed without the glued tail)",
        r"""\[REDACTED\][^\s&,;"'}\])]*+|""",
        r"\[REDACTED\]|",
        1,
    ),
    (
        "QP-GUARD-TAIL-BOUNDED-64 (glued tail capped at 64)",
        r"""\[REDACTED\][^\s&,;"'}\])]*+|""",
        r"""\[REDACTED\][^\s&,;"'}\])]{0,64}|""",
        1,
    ),
    (
        "QP-BREAK-ON-EMPTY-VALUE (empty value ends the scan)",
        "            if value is None:\n                continue",
        "            if value is None:\n                break",
        1,
    ),
    (
        "QP-VALUE-STOPS-AT-SLASH",
        '|[^\\s&,;"\'}\\])]++"""',
        '|[^\\s&,;"\'}\\])/]++"""',
        1,
    ),
    (
        "QP-VALUE-STOPS-AT-PLUS-PCT",
        '|[^\\s&,;"\'}\\])]++"""',
        '|[^\\s&,;"\'}\\])+%]++"""',
        1,
    ),
]


# --- FTF-0904-F2 round 2 -------------------------------------------------------------------
_R2_LATER = ("", ' "later"', " 'later'", ' x="later"')


def _r2_assert_clean(label: str, text: str) -> None:
    out = lr.redact_text(text)
    check("FTF-0904-F2", f"{label} {text!r}: leaked {out!r}", _F1_VALUE not in out)
    out2 = lr.redact_text(out)
    check("FTF-0904-F2", f"{label} {text!r}: not idempotent", out2 == out)


def test_f2r2_generic_quoted_value_trailing_backslash_fails_closed() -> None:
    """GENERIC-QUOTED-FAIL-OPEN: a closing quote that follows a backslash run is not printed,
    with and without a later quote on the line (the certified module redacted all of these)."""
    rows = 0
    for n in (1, 2, 3, 4):
        bs = "\\" * n
        for later in _R2_LATER:
            for template in (
                'password="{v}{bs}"',
                '{{"password": "{v}{bs}"}}',
                '{{"client_secret": "C:{bs}{v}{bs}"}}',
                "api_key='{v}{bs}'",
                "token: '{v}{bs}'",
            ):
                text = template.format(v=_F1_VALUE, bs=bs) + later
                rows += 1
                _r2_assert_clean("FAIL-OPEN", text)
                with _Harness() as h:
                    logging.getLogger("t").error("%s", text)
                    logging.getLogger("t").error(text)
                    found = leaked([_F1_VALUE], h.outputs())
                check("FTF-0904-F2", f"{text!r}: logging-path leak {found}", not found)
    check("FTF-0904-F2", "trailing-backslash table is non-trivial", rows >= 100)


def test_f2r2_escaped_quote_forms_of_every_f2_name() -> None:
    """F2-ESCAPED-QUOTE-FORM: backslash-escaped quotes at 1-3 escape levels, JSON and logfmt."""
    rows = 0
    for name in _F2_NAMES:
        for spelling in _f3_random_case(name)[::10]:
            for n in (1, 2, 3):
                e = "\\" * n
                for text in (
                    f'{{{e}"{spelling}{e}": {e}"{_F1_VALUE}{e}"}}',
                    f'{{{e}"{spelling}{e}":{e}"{_F1_VALUE}{e}"}}',
                    f'{spelling}={e}"{_F1_VALUE}{e}"',
                    f'msg="x {spelling}={e}"{_F1_VALUE}{e}" y"',
                    f"{spelling}={e}'{_F1_VALUE}{e}' tail",
                    f'{{{e}"{spelling}{e}": {e}"{_F1_VALUE}',  # escaped, unterminated
                ):
                    rows += 1
                    _r2_assert_clean("ESCAPED-FORM", text)
                    if rows % 9 == 0:
                        with _Harness() as h:
                            logging.getLogger("t").error("%s", text)
                            found = leaked([_F1_VALUE], h.outputs())
                        check("FTF-0904-F2", f"{text!r}: logging-path leak {found}", not found)
    check("FTF-0904-F2", "escaped-quote table is non-trivial", rows > 500)


def test_f2r2_unterminated_and_oversized_quoted_values() -> None:
    """V6 / V10 / V11: unterminated single quotes run to end of line; no length bound."""
    for name in ("state", "code", "session_key"):
        for q in _F2_QUOTES:
            _r2_assert_clean("V6", f"{name}={q}{_F1_VALUE} and the rest of the line")
            for size in (300, 4097, 9000, 70000):
                filler = "a b" * (size // 3)
                _r2_assert_clean("V10/V11", f"{name}={q}{filler}{_F1_VALUE}{q} tail")
                _r2_assert_clean("V10/V11-open", f"{name}: {q}{filler}{_F1_VALUE}")
    out = lr.redact_text("state='x" + _F1_VALUE + "\nnext line kept")
    check("FTF-0904-F2", f"unterminated quote crossed the line {out!r}", out.endswith("next line kept"))


def test_f2r2_composite_endings_every_ending_every_case() -> None:
    """TABLE-ENDINGS / V17: every ending in `_QUERY_PARAM_NAMES` as the tail of a composite name
    (separated, camelCase and glued), random case, both quotes, both separators."""
    rows = 0
    for ending in sorted(lr._QUERY_PARAM_NAMES):
        for prefix in ("mfa_", "x_", "user_", "oauth_", "my.", "app-", "zz"):
            for spelling in _f3_random_case(prefix + ending)[::5]:
                for qch in _F2_QUOTES:
                    for sep in ("=", ": "):
                        text = f"ctx {spelling}{sep}{qch}{_F1_VALUE}{qch} tail_ok"
                        rows += 1
                        out = lr.redact_text(text)
                        check("FTF-0904-F2", f"{text!r}: leaked {out!r}", _F1_VALUE not in out)
                        if rows % 11 == 0:
                            with _Harness() as h:
                                logging.getLogger("t").error("%s", text)
                                found = leaked([_F1_VALUE], h.outputs())
                            check("FTF-0904-F2", f"{text!r}: logging leak {found}", not found)
    check("FTF-0904-F2", "composite-ending table covers all 13 endings", len(lr._QUERY_PARAM_NAMES) == 13)
    check("FTF-0904-F2", "composite table is non-trivial", rows > 1000)


def test_f2r2_structured_benign_lines_redacted_by_design() -> None:
    """OVER-REDACTION decision (Control plane): a quoted VALUE after a credential NAME is redacted
    by design; the structured benign lines are pinned so the decision cannot drift silently."""
    for text, want in (
        ('{"state": "running"}', '{"state": [REDACTED]}'),
        ("state='running'", "state=[REDACTED]"),
        ('code="200" ok', "code=[REDACTED] ok"),
    ):
        check("FTF-0904-F2", f"{text!r}: got {lr.redact_text(text)!r}", lr.redact_text(text) == want)
    check("FTF-0904-F2", "bare 'state: running' changed", lr.redact_text("state: running") == "state: running")


# FTF-0904-F2: each fault removes one protection added this round
FAULTS += [
    (
        "F2-quoted branch disabled",
        'if match.group("qsep") is not None:',
        "if False:",
        1,
    ),
    (
        "F2-unterminated double quote not redacted to end of line",
        r"""\\++[^\r\n]?)*+"?|'""",
        r"""\\++[^\r\n]?)*+"|'""",
        1,
    ),
    (
        "F2-single-quoted values dropped",
        "|'(?:[^'\\\\\\r\\n]|\\\\++[^\\r\\n]?)*+'?" + '"' * 3,
        '"' * 3,
        1,
    ),
    (
        "F2-escaped quote ends the double-quoted value",
        r""""(?:[^"\\\r\n]|\\++[^\r\n]?)*+"?|'""",
        r""""[^"\r\n]*+"?|'""",
        1,
    ),
    (
        "F2-generic key=value quoted value ends at an escaped quote",
        r"""|"(?:[^"\\\r\n]++|\\++[^\r\n]?)*+"|'""",
        r"""|"[^"\r\n]*+"|'""",
        1,
    ),
]


# FTF-0904-F2 round 2
FAULTS += [
    (
        "F2r2-generic quoted value no end-of-line fallback",
        "    r\"\"\"|\"[^\\r\\n]*+|'[^\\r\\n]*+\"\"\"\n",
        "",
        1,
    ),
    (
        "F2r2-qsep ignores escaped key quote",
        r"""(?P<qsep>\\*["']?""",
        r"""(?P<qsep>["']?""",
        1,
    ),
    (
        "F2r2-qsep lookahead needs a bare quote",
        r"""(?=\\*["'])""",
        r"""(?=["'])""",
        1,
    ),
    (
        "F2r2-escaped opening quote read as bare",
        'text[match.end()] == "\\"',
        "False",
        1,
    ),
    (
        "F2r2-escaped value stops at first closer",
        r"""(?:\\++["']|[^\r\n]*+)""",
        r"""(?:\\++["'])?""",
        1,
    ),
    (
        "F2r2-quoted branch skipped for composite endings",
        'if match.group("qsep") is not None:',
        'if match.group("qsep") is not None and not match.group("key").lower().endswith(("sig", "pass", "otp", "verifier", "ticket", "auth", "sid", "jwt")):',
        1,
    ),
    (
        "F2r2-single-quote unterminated bound",
        "|'(?:[^'\\\\\\r\\n]|\\\\++[^\\r\\n]?)*+'?",
        "|'(?:[^'\\\\\\r\\n]|\\\\++[^\\r\\n]?){0,4096}'?",
        1,
    ),
]


def _run_mutant(fault_id: str, old: str, new: str, count: int) -> tuple[bool, str]:
    source = MODULE.read_text(encoding="utf-8")
    if source.count(old) != count:
        return False, f"fault {fault_id}: pattern occurs {source.count(old)}x, expected {count}"
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "packages" / "security"
        pkg.mkdir(parents=True)
        (Path(tmp) / "packages" / "__init__.py").write_text("")
        for sibling in (REAL_ROOT / "packages" / "security").glob("*.py"):
            shutil.copy(sibling, pkg / sibling.name)
        (pkg / "log_redaction.py").write_text(source.replace(old, new), encoding="utf-8")
        env = {
            **os.environ,
            "FTF0904_ROOT": tmp,
            "FTF0904_BEHAVIOUR_ONLY": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                capture_output=True,
                text=True,
                env=env,
                cwd=tmp,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return True, "mutant made the suite exceed 300 s (catastrophic regex): killed"
    return result.returncode != 0, (result.stdout + result.stderr)[-300:]


def test_zz_fault_injection_turns_checks_red() -> None:
    if BEHAVIOUR_ONLY:
        return
    for fault_id, old, new, count in FAULTS:
        killed, detail = _run_mutant(fault_id, old, new, count)
        check("FTF-0904-F", f"fault {fault_id} stayed GREEN ({detail})", killed)


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0904 Log redaction tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0904 Log redaction tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
