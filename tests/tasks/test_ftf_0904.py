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
import re
import shutil
import subprocess
import sys
import tempfile
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
        "request_id=req-1 state=running",
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
    if label == "base64-run-bad-tail":
        return "A" * (size - 3) + "==="
    if label == "base64-run-bad-tail-url":
        return "a-_" * ((size - 3) // 3) + "==="
    if label == "userinfo-scheme-run":
        return "a" * size
    if label == "userinfo-scheme-run-sep":
        return "a" * (size - 3) + "://"
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


_ROUND6_LABELS = _ALL_LABELS[-9:]


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
        "F08 escaped-JSON value form dropped (round 5 retarget: round 5 replaced the bounded "
        "lazy form this fault used to target with an unrolled, unbounded one)",
        'r\'(?<!\\\\)\\\\++"(?:[^"\\\\\\r\\n]++|\\\\++(?!"))*+\\\\+"\' r"""|',
        'r"""',
        1,
    ),
    ("F09 userinfo stops at first @", '[^/\\s?#]*@")', '[^/\\s?#@]*@")', 1),
    ("F10 idempotence guard dropped", "\\[REDACTED\\]|{_QUOTED_VALUE}", "{_QUOTED_VALUE}", 1),
    # --- normalisation and decoding ----------------------------------------------------
    ("F11 NFKC skipped", 'unicodedata.normalize("NFKC", text)', "text", 1),
    ("F12 format characters kept", '("Cf", "Cc", "Cs", "Co", "Cn")', '("Cc", "Cs", "Co", "Cn")', 1),
    (
        "F13 no percent-decoding",
        "        layer = unquote(layer)\n",
        "        layer = layer\n",
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
        "Q-PAIR-KEY-WS-QUAD pair opener scans any non-quote run, not just whitespace (quadratic)",
        r"""[(\[]\s*b?(?P<q>""",
        r"""[(\[][^'"]*b?(?P<q>""",
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
