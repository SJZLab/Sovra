"""FTF-0904 -- Log redaction (Section 11-12, 65.4, 66, 70, 77, 81).

Section 65.4: "no API key in URL, git config, log, trace ..."; Section 46:
the logging sanitizer redacts Authorization headers, API keys, tokens and
secret-shaped URLs. This module is that sanitizer for stdlib `logging`:

* `redact_text(text)` -- removes known secret shapes from a string: secret
  header values (Authorization, Proxy-Authorization, Cookie, Set-Cookie,
  X-Api-Key and the like, quoted or not, in dict reprs and JSON), URL userinfo,
  secret-named `key=value` pairs and query parameters, bearer/basic
  credentials, well-known token prefixes (sk-, ghp_, github_pat_, xox*-,
  AKIA..., hf_, AIza..., JWT-shaped), and PEM private-key blocks. Text is first
  NFKC-normalised with format/control characters removed, so fullwidth,
  NBSP/ideographic-space and zero-width-split look-alikes meet the same rules;
  percent-encoded forms (up to three layers) and base64-wrapped forms (two
  layers) are decoded for *detection* and the whole decoded/wrapped run is
  replaced when it hides a secret.
* `redact_exception(exc)` -- the fully rendered traceback of the exception,
  its `__cause__` / `__context__` chain, `__notes__` and exception groups
  (cycle-safe, via the stdlib traceback machinery), then `redact_text`.
* `redact_value(obj)` -- structured values (mappings with secret-named keys,
  sequences, bytes, arbitrary objects via `repr`), bounded in depth and
  cycle-safe, never mutating its input.
* Ingress: `install_redaction()` wraps the logging record factory, so every
  record is rendered (`msg % args`, which also joins secrets split across
  arguments) and scrubbed at creation, before any handler, filter or `logging`
  error path can see the raw message, args or `exc_info`.
* Sink: `RedactingFilter` + `RedactingFormatter` (`attach_sink(handler)`)
  scrub again at every handler -- including `extra=` attributes, which are
  attached to the record only after the factory returns -- and redact the final
  formatted line.

Fail closed (Section 65.4 / negative acceptance): if any rule, `__str__`,
`__repr__`, `%`-format or size check fails, nothing raw is logged. Library
calls raise `LogRedactionError` (constant message, stable `code`, no chained
cause); logging paths substitute the constant `REDACTION_FAILED_MARKER` for the
message, args, traceback and every non-standard attribute. An error never
carries caller text.

Redaction is not a licence to export content. This module has no knowledge of,
and builds nothing for, any external collector: the content-free emitter
remains the only external telemetry path and still refuses free text,
redacted or not. A redacted line is safe to keep in a private log, not safe to
ship out.

Disclosed limits: detection is pattern-based. A secret with no known prefix,
no secret-named key and no header/URL context (a bare opaque string) is not
recognised -- that is what the canary-secret ledger and the content-free
telemetry schemas are for. Key names are matched by substring, so benign keys
such as `max_tokens` are redacted too (over-redaction by design). Handlers
added after `install_redaction` are covered at ingress (message, args,
exception) but need `attach_sink` to scrub `extra=` attributes. This module is a
pure library: no live logging configuration installs it yet; that wiring is a
separate dependency-gated task, disclosed in docs/spec-matrix.yaml with a blank
`proving_symbol`.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import traceback
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote

__all__ = [
    "LogRedactionError",
    "MAX_TEXT_LENGTH",
    "REDACTED",
    "REDACTION_FAILED_MARKER",
    "RedactingFilter",
    "RedactingFormatter",
    "attach_sink",
    "install_redaction",
    "redact_exception",
    "redact_text",
    "redact_value",
]

REDACTED = "[REDACTED]"
REDACTION_FAILED_MARKER = "[LOG_REDACTION_FAILED]"
MAX_TEXT_LENGTH = 262_144
_MAX_DEPTH = 8
_MAX_DECODE_LAYERS = 3
_BASE64_LAYERS = 2


class LogRedactionError(Exception):
    """Redaction could not be completed. Constant message, stable `code`, and
    never any caller text -- not the payload, not a field name."""

    code = "LOG_REDACTION_FAILED"

    def __init__(self) -> None:
        super().__init__("log redaction failed")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

_HEADER_KEYS = (
    r"(?:proxy-)?authorization|(?:set-)?cookie|x-api-key|x-auth-token|x-amz-security-token"
    r"|x-goog-api-key|x-access-token|x-csrf-token|api-key"
)
_SECRET_KEY_WORDS = (
    r"(?:pass(?:word|wd|phrase)|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key"
    r"|credential|session[_\-]?id|signature|auth[_\-]?(?:key|header))"
)
_SECRET_KEY = rf"[\w.\-]*{_SECRET_KEY_WORDS}[\w.\-]*"
_SEP = r"""\\*["']?[ \t]*[:=][ \t]*"""
_QUOTED_VALUE = r"""\\+"[^"\r\n]*?\\+"|"[^"\r\n]*"|'[^'\r\n]*'"""

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.S,
)
_HEADER = re.compile(
    rf"(?P<key>(?<![\w\-])(?:{_HEADER_KEYS}))(?P<sep>{_SEP})(?P<val>{_QUOTED_VALUE}|[^\r\n]*)",
    re.I,
)
_BEARER = re.compile(r"""(?<![A-Za-z])(?P<scheme>bearer|basic)[ \t]+[^\s,;"'}]{8,}""", re.I)
_PREFIXES = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprse]-[A-Za-z0-9\-]{8,}"
    r"|(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|AIza[0-9A-Za-z_\-]{35,}"
    r"|eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*"
    r")",
    re.I,
)
_USERINFO = re.compile(r"(?P<scheme>(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s?#]*@")
_KEY_VALUE = re.compile(
    rf"(?P<key>(?<![\w.\-]){_SECRET_KEY})(?P<sep>{_SEP})"
    rf"""(?P<val>\[REDACTED\]|{_QUOTED_VALUE}|[^\s&,;"'}}\])]+)""",
    re.I,
)
_BASE64_RUN = re.compile(
    r"(?<![A-Za-z0-9+/_\-])(?P<body>[A-Za-z0-9+/_\-]{16,})={0,2}(?![A-Za-z0-9+/_=\-])"
)

# (name, compiled rule). The replacement for a rule is `_REPLACEMENTS[name]`
# (default: the bare marker). Rules run in this order: whole-line header values
# first, so a `Bearer x` inside one is gone before the key=value rule sees it.
_RULES: tuple[tuple[str, Any], ...] = (
    ("pem", _PEM),
    ("header", _HEADER),
    ("bearer", _BEARER),
    ("prefix", _PREFIXES),
    ("userinfo", _USERINFO),
    ("key_value", _KEY_VALUE),
)
_REPLACEMENTS: Mapping[str, str] = {
    "header": rf"\g<key>\g<sep>{REDACTED}",
    "bearer": rf"\g<scheme> {REDACTED}",
    "userinfo": rf"\g<scheme>{REDACTED}@",
    "key_value": rf"\g<key>\g<sep>{REDACTED}",
}

_SECRET_KEY_NAME = re.compile(rf"(?:{_HEADER_KEYS})|{_SECRET_KEY}", re.I)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(
        ch
        for ch in text
        if ch in "\t\n\r" or (unicodedata.category(ch) not in ("Cf", "Cc", "Cs", "Co", "Cn"))
    )


def _apply_rules(text: str) -> str:
    for name, rule in _RULES:
        text = rule.sub(_REPLACEMENTS.get(name, REDACTED), text)
    return text


def _decode_base64(body: str) -> str | None:
    """The UTF-8 text a base64/base64url run decodes to, or None. Anything that decodes
    is scanned (binary with an embedded secret included): fail closed, not selective."""
    cleaned = body.replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def _scan(text: str, layers: int) -> str:
    text = _apply_rules(text)
    if layers <= 0:
        return text

    def _wrap(match: re.Match[str]) -> str:
        decoded = _decode_base64(match.group("body"))
        if decoded is not None and _scan(decoded, layers - 1) != decoded:
            return REDACTED
        return match.group(0)

    return _BASE64_RUN.sub(_wrap, text)


def _redact(text: str) -> str:
    if len(text) > MAX_TEXT_LENGTH:
        raise LogRedactionError
    text = _normalise(text)
    layers = [text]
    for _ in range(_MAX_DECODE_LAYERS):
        decoded = unquote(layers[-1])
        if decoded == layers[-1]:
            break
        layers.append(decoded)
    for layer in layers[1:]:
        scanned = _scan(layer, _BASE64_LAYERS)
        if scanned != layer:
            return scanned
    return _scan(text, _BASE64_LAYERS)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def redact_text(text: str) -> str:
    """Return `text` with known secret shapes replaced. Raises
    `LogRedactionError` (never with caller text) on a non-`str`, an oversize
    input, or any failure of a rule."""
    if type(text) is not str:
        raise LogRedactionError
    failed = False
    try:
        result = _redact(text)
    except Exception:
        failed = True
    if failed:
        raise LogRedactionError from None
    return result


def redact_exception(exc: BaseException) -> str:
    """The redacted, fully rendered traceback of `exc` and its whole chain."""
    failed = False
    try:
        rendered = "".join(traceback.format_exception(exc))
        result = redact_text(rendered)
    except Exception:
        failed = True
    if failed:
        raise LogRedactionError from None
    return result


def redact_value(value: object) -> object:
    """A redacted copy of a structured value. Mappings drop the value of any
    secret-named key; strings, bytes and arbitrary objects are redacted via text."""
    failed = False
    try:
        result = _redact_value(value, 0, set())
    except Exception:
        failed = True
    if failed:
        raise LogRedactionError from None
    return result


def _redact_value(value: object, depth: int, path: set[int]) -> object:
    if value is None or type(value) in (bool, int, float):
        return value
    if isinstance(value, str):
        return redact_text(str.__str__(value))
    if depth >= _MAX_DEPTH or id(value) in path:
        return REDACTED
    if isinstance(value, bytes | bytearray):
        return redact_text(bytes(value).decode("utf-8", "replace"))
    path = path | {id(value)}
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            name = redact_text(key if type(key) is str else repr(key))
            if _SECRET_KEY_NAME.fullmatch(name):
                out[name] = REDACTED
            else:
                out[name] = _redact_value(item, depth + 1, path)
        return out
    if isinstance(value, list | tuple):
        items = [_redact_value(item, depth + 1, path) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, set | frozenset):
        return [_redact_value(item, depth + 1, path) for item in value]
    return redact_text(repr(value))


# ---------------------------------------------------------------------------
# logging integration: ingress (record factory) and sink (filter + formatter)
# ---------------------------------------------------------------------------

_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def _scrub_record(record: logging.LogRecord) -> None:
    try:
        message = redact_text(str.__str__(record.getMessage()))
        exc_text = None
        exc_info = record.exc_info
        if exc_info:
            value = exc_info[1] if isinstance(exc_info, tuple) else exc_info
            if isinstance(value, BaseException):
                exc_text = redact_exception(value)
        elif record.exc_text:
            exc_text = redact_text(str.__str__(record.exc_text))
        stack_info = record.stack_info
        if stack_info is not None:
            stack_info = redact_text(str.__str__(stack_info))
        record.msg = message
        record.args = ()
        record.exc_info = None
        record.exc_text = exc_text
        record.stack_info = stack_info
    except Exception:
        record.msg = REDACTION_FAILED_MARKER
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
    extras = [k for k in vars(record) if k not in _STANDARD_ATTRS]
    try:
        scrubbed = {
            k: REDACTED
            if _SECRET_KEY_NAME.fullmatch(k)
            else _redact_value(record.__dict__[k], 0, set())
            for k in extras
        }
    except Exception:
        scrubbed = dict.fromkeys(extras, REDACTION_FAILED_MARKER)
    record.__dict__.update(scrubbed)


class RedactingFilter(logging.Filter):
    """Sink-side: scrubs every record a handler is about to emit (idempotent)."""

    def filter(self, record: logging.LogRecord) -> bool:
        _scrub_record(record)
        return True


class RedactingFormatter(logging.Formatter):
    """Redacts the final formatted line and any traceback it renders. With
    `inner`, delegates the layout to an existing formatter and redacts its output."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        *,
        inner: logging.Formatter | None = None,
    ) -> None:
        super().__init__(fmt, datefmt)
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        try:
            raw = self._inner.format(record) if self._inner is not None else super().format(record)
            return redact_text(str.__str__(raw))
        except Exception:
            return REDACTION_FAILED_MARKER


def attach_sink(handler: logging.Handler) -> None:
    """Make `handler` redact whatever reaches it, whatever formatter it uses."""
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter())
    existing = handler.formatter
    if not isinstance(existing, RedactingFormatter):
        handler.setFormatter(RedactingFormatter(inner=existing))


def _wrap_factory(original: Callable[..., logging.LogRecord]) -> Callable[..., logging.LogRecord]:
    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(*args, **kwargs)
        _scrub_record(record)
        return record

    factory.__wrapped__ = original  # type: ignore[attr-defined]
    factory._ftf0904_redacting = True  # type: ignore[attr-defined]
    return factory


def install_redaction(root: logging.Logger | None = None) -> None:
    """Ingress + sink for `root` (default: the root logger). Idempotent."""
    current = logging.getLogRecordFactory()
    if not getattr(current, "_ftf0904_redacting", False):
        logging.setLogRecordFactory(_wrap_factory(current))
    for handler in (root or logging.getLogger()).handlers:
        attach_sink(handler)
