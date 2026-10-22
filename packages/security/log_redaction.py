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
  the raw text is always scanned first; percent-encoded forms (up to three
  layers) and base64-wrapped forms (two layers) are decoded for *detection*
  only -- a decoded layer is never returned, and when one hides a secret the raw
  text from that encoded token to the end of the line (the whole wrapped run for
  base64) is replaced.
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
  `Logger.makeRecord` is wrapped too, because `extra=` attributes are attached
  only after the factory returns: every logger (including `propagate=False`
  ones such as `uvicorn.error`, with handlers added before or after install)
  hands its handlers an already-scrubbed record.
* Sink: `RedactingFilter` + `RedactingFormatter` (`attach_sink(handler)`)
  scrub again at every handler and redact the final formatted line.

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
such as `max_tokens` are redacted too (over-redaction by design). Records that bypass
the logger (`logging.makeLogRecord`) are covered only at a handler with
`attach_sink`. Detection runs in time linear in the input AND every rule is
unbounded in the length of secret it redacts (Section 65.4/round-5 ruling: a
bounded match is a leak, not a performance safeguard) -- the key rule and the
bare-quoted-value shapes are possessive (the closing quote is excluded from the
class that scans for it, so there is only ever one possible split point --
backtracking could never find a different one, only waste time). JWT
shape is found per maximal `[A-Za-z0-9_\\-.]` run (split on dots once, first "eyJ" per segment
searched once), not by a regex that re-scans at every "eyJ" repeat: that keeps it linear with no
bound on the segment length AND still redacts a JWT glued after `_`/`-` (`token_<jwt>`,
`sess-<jwt>`), which the round-5 `(?<![_\\-])` lookbehind let through (round 6).
The escaped-quoted-value shape allows backslashes in its own middle, so it cannot simply be
made possessive the way the bare-quote shapes are; round 4 bounded its content to 8192 chars,
which is the same leak-above-the-cap problem. Round 5 replaces the bound with
an unrolled loop (alternating a possessive run of plain chars with a
possessive backslash run that is not immediately followed by the closing
quote) plus the same not-preceded-by-backslash exclusion on the opening run,
so a lone, unterminated backslash run and a backslash run embedded in the
middle of a real value are both a single linear pass, with no upper bound on
how much of the value gets redacted. Percent-encoded-secret candidates are
found by splitting on whitespace once (not by backtracking a greedy `\\S*` at
every character position, which was quadratic on a long run with no closing
hex pair), and each of the bounded cut rounds then does at most
`log2(candidates)` `_reveals_secret` probes. This module is a pure library: no
live logging configuration installs it yet; that wiring is a separate
dependency-gated task, disclosed in docs/spec-matrix.yaml with a blank
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

_HEADER_NAMES = (
    "proxy-authorization",
    "authorization",
    "set-cookie",
    "cookie",
    "x-api-key",
    "x-auth-token",
    "x-amz-security-token",
    "x-goog-api-key",
    "x-access-token",
    "x-csrf-token",
    "api-key",
)
# `Authorization` as a header, and `HTTP_AUTHORIZATION` as the WSGI/CGI environ spelling of it
# (a bare `api_key` stays with the key=value rule: a header value runs to the end of the line).
_HEADER_KEYS = (
    "|".join(_HEADER_NAMES)
    + "|http_(?:"
    + "|".join(name.replace("-", "_") for name in _HEADER_NAMES)
    + ")"
)
_SECRET_KEY_WORDS = (
    r"(?:pass(?:word|wd|phrase)|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key"
    r"|credential|session[_\-]?id|signature|auth[_\-]?(?:key|header))"
)
# One maximal run of key characters (possessive, so a failed separator never backtracks into
# it) that contains a secret word (a lookahead that runs once per run): linear in the input.
_SECRET_KEY = rf"(?=[\w.\-]*?{_SECRET_KEY_WORDS})[\w.\-]++"
_SEP = r"""\\*["']?[ \t]*[:=][ \t]*"""
# Possessive `*+` on the bare-quote alternatives: the closing quote is itself excluded from the
# preceding class, so there is only ever one possible split point -- giving back characters on
# failure can never find a different one, only waste time re-checking positions already ruled
# out. The escaped-quote alternative's middle allows backslashes (a real nested-JSON escape can
# contain them), so its content cannot simply exclude backslashes the way the bare-quote
# alternatives exclude their own quote. Round 4 bounded it to 8192 chars instead -- a leak above
# the cap, not a real fix (round 5). Round 5 unrolls it instead, the same technique `_PAIR`'s own
# value already uses: content is a possessive run of plain chars, OR a possessive backslash run
# that is NOT immediately followed by the closing quote (so a backslash run that *is* followed
# by a quote is left alone for the closing `\\+"` to consume, with no backtracking either way).
# `(?<!\\)` on the opening run means only the first backslash of a maximal backslash run can ever
# start an attempt -- the same "one start per run" fix as the JWT shape below -- so a long,
# unterminated backslash run (no second quote at all) is one linear scan, not O(n) restarts.
# Round 6 (F2 r2, BACKSLASH-PARITY): the bare-quote alternatives' own backslash branch used to be
# `\\++[^\r\n]?` -- the whole run, then ANY one more character, whatever it is. That always
# swallows the character right after the run, including the closing quote itself, whatever the
# run's length: an EVEN-length run (`\\` pairs only, each one a literal backslash, nothing left to
# escape) still ate the real terminator (`"V\\"` -- two backslashes, a plain closed string --
# matched nothing at all through `_QUOTED_VALUE`, or over-ran into the text past the real close
# through `_QUOTED_SECRET`'s optional one). Standard escaping pairs backslashes two at a time;
# only a leftover, UNPAIRED single backslash escapes the next character. `(?:\\\\)++` consumes
# whole pairs (still possessive, still one pass); the trailing `(?:\\[^\r\n])?` fires only when one
# backslash is left unpaired, and that lone case is `\\[^\r\n]` on its own for a run of exactly one.
# A paired (even) run never reaches the optional escape, so the real closing quote is left for the
# literal `"`/`'` below, exactly as an even run of literal backslashes should.
# Round 3 (F2 r3, UNTERMINATED-FAILS-CLOSED): with escape-aware bodies, a value whose last quote
# is escaped (`password="V\\"`, an odd backslash run and no later quote) has no closing quote, so
# the bare-quote alternative used to fail and the generic pair rule printed the secret, which
# certified FTF-0904 redacts. An unterminated quoted value now runs to the end of the line (fail
# closed); a terminated one still ends at its real closing quote.
_QUOTED_VALUE = (
    r'(?<!\\)\\++"(?:[^"\\\r\n]++|\\++(?!"))*+\\+"'
    r"""|"(?:[^"\\\r\n]++|(?:\\\\)++(?:\\[^\r\n])?|\\[^\r\n])*+(?:"|(?=[\r\n])|\Z)"""
    r"""|'(?:[^'\\\r\n]++|(?:\\\\)++(?:\\[^\r\n])?|\\[^\r\n])*+(?:'|(?=[\r\n])|\Z)"""
)

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    re.S,
)
_HEADER = re.compile(
    rf"(?P<key>(?<![\w\-])(?:{_HEADER_KEYS}))(?P<sep>{_SEP})(?P<val>{_QUOTED_VALUE}|[^\r\n]*)",
    re.I,
)
_PAIR = re.compile(
    rf"""(?P<pre>[(\[]\s*b?(?P<q>["'])(?:{_HEADER_KEYS}|{_SECRET_KEY})(?P=q)\s*,\s*)"""
    r"""b?(?P<vq>["'])(?:\\.|(?!(?P=vq))[^\\])*(?P=vq)""",
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
    r")",
    re.I,
)
# JWT-shaped spans are found per run, not by a regex that must re-scan from every "eyJ": a
# regex whose segments are unbounded re-consumes the rest of a dot-free run at every "eyJ" repeat
# (quadratic), and one that refuses a start after `_`/`-` (the round-5 form) lets `token_<jwt>`,
# `sess-<jwt>` and the like through. Instead each maximal run of `[A-Za-z0-9_\-.]` is split on
# its dots once and each segment is searched once for its first "eyJ" (not glued after an
# alphanumeric): linear in the run, no bound on how much is redacted.
_JWT_RUN = re.compile(r"[A-Za-z0-9_\-.]++")
_JWT_MIN_SEGMENT = 5


def _jwt_start(segment: str) -> int:
    """Offset of the first "eyJ" in `segment` that is not preceded by an alphanumeric and has at
    least `_JWT_MIN_SEGMENT` characters after it, or -1. Only the first candidate is tried: any
    later one has fewer characters left."""
    lowered = segment.lower()  # the rule has always been case-insensitive
    pos = lowered.find("eyj")
    while pos > 0 and segment[pos - 1].isalnum():
        pos = lowered.find("eyj", pos + 1)
    return pos if pos >= 0 and len(segment) - pos - 3 >= _JWT_MIN_SEGMENT else -1


def _redact_jwts_in_run(match: re.Match[str]) -> str:
    run = match.group(0)
    segments = run.split(".")
    out: list[str] = []
    i = 0
    while i < len(segments):
        start = _jwt_start(segments[i]) if i + 2 < len(segments) else -1
        if start >= 0 and len(segments[i + 1]) >= _JWT_MIN_SEGMENT:
            out.append(segments[i][:start] + REDACTED)
            i += 3
        else:
            out.append(segments[i])
            i += 1
    return ".".join(out)


_USERINFO = re.compile(r"(?P<scheme>(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s?#]*@")
_KEY_VALUE = re.compile(
    rf"(?P<key>(?<![\w.\-]){_SECRET_KEY})(?P<sep>{_SEP})"
    rf"""(?P<val>\[REDACTED\]|{_QUOTED_VALUE}|[^\s&,;"'}}\])]+)""",
    re.I,
)
# FTF-0904-F1: generic credential-carrying parameter names (`?key=`, `?sig=`, OAuth `?code=` and
# `?state=` on a callback, session ids, one-time codes). They cannot join `_SECRET_KEY_WORDS`: that
# list matches a word *inside* a key run and also binds structured keys, so `state`/`code`/`key`
# would redact `{"state": "running"}` and `monkey=1`. Round 2 (QP-NAME-CLASS, QP-POS-SERIALIZED):
# the rule binds a parameter *name class* -- the LAST word of the name (split on `_ - .` and
# camelCase) is one of `_QUERY_PARAM_NAMES` -- so `session_key`, `sessionKey`, `auth_code`,
# `oauth_verifier`, `PHPSESSID` and `connect.sid` are covered, while `keyboard=`, `codec=`,
# `monkey=`, `state_of=` and `code_path=` (last word not a name) survive. The name needs no `?` in
# front of it: a query serialized without one (`str(request.query_params)`), a urlencoded form body
# and a name after a space/quote/`/` are the same leak. Over-redaction (`state=CA`, prose `code=5`)
# is FTF-0904's design.
# (`totp`, `hotp`, `sessid` and `phpsessid` need no entry: they end in `otp` / `sid`.)
_QUERY_PARAM_NAMES = frozenset(
    {
        "key",
        "sig",
        "auth",
        "session",
        "jwt",
        "code",
        "state",
        "verifier",
        "ticket",
        "otp",
        "pass",
        "sid",
        "jsessionid",
    }
)
# One start per maximal key run (lookbehind) and a possessive run: linear, no restart inside a run.
# F2: a QUOTED value after a credential name (`state="v"`, `code: 'v'`, JSON `"state": "v"`) is read
# up to its closing quote (a quote after an ODD-length run of backslashes is escaped, so a
# JSON-re-encoded `\\"` cannot end it early -- an EVEN-length run is a run of literal backslashes
# with nothing left over to escape anything, so the quote right after it is the real close, not
# more content; an unterminated quote runs to end of line, deny by default). The bare `name: value`
# form is not a query position and stays unredacted.
# Round 3 (F2 r3, ESCAPED-QUOTE-FORM): a JSON-escaped key (`\"state\":`) or an escaped opening
# value quote (`: \"V\"`) left `qsep`'s lookahead unmet (it required a literal `"`/`'` right after
# the separator), so `_QUOTED_SECRET.match` was never even attempted and the whole match (key
# included) was skipped -- a total leak, not a truncation. `qsep` now accepts a backslash run
# before the key's own closing quote, exactly as `_SEP` already does for the header/key_value
# rules, and its lookahead accepts a backslash run before the value's opening quote too -- the
# backslash itself is left unconsumed (not swallowed into `qsep`), for `_QUOTED_SECRET`'s own new
# escaped-open alternatives below to match, the same split `_HEADER`/`_KEY_VALUE` already use with
# `_SEP` + `_QUOTED_VALUE`.
_QUERY_ASSIGN = re.compile(
    r"""(?<![\w.\-])(?P<key>[\w.\-]++)"""
    r"""(?:(?P<qsep>\\*["']?[ \t]*[:=][ \t]*)(?=\\*["'])|(?P<sep>=))"""
)
# Round 6 (F2 r2, BACKSLASH-PARITY, same fix as `_QUOTED_VALUE` above): the backslash branch used
# to be `\\++[^\r\n]?` -- the whole run, then any one more character regardless of parity -- so an
# even (paired, nothing escaped) run still swallowed the real closing quote, over-redacting into
# whatever followed it on the line. `(?:\\\\)++` consumes whole literal-backslash pairs; only a
# leftover single backslash (odd run) pairs with the next character via `(?:\\[^\r\n])?` / the
# lone `\\[^\r\n]`, leaving an even run's closing quote for the optional literal `"?`/`'?` instead.
# Round 3 (F2 r3, ESCAPED-QUOTE-FORM): the two alternatives below only ever matched a value whose
# OPENING quote is a literal `"`/`'` -- `_QUERY_ASSIGN`'s lookahead now also admits a backslash run
# before that opening quote (JSON-escaped `\"state\": \"V\"`, logfmt `code=\"V\"`), left
# unconsumed for this regex to match. The two new LEADING alternatives mirror `_QUOTED_VALUE`'s own
# escaped-open branch exactly: a possessive backslash run (one start per run, `(?<!\\)`) then the
# opening quote, content that is either plain chars or a backslash run NOT immediately followed by
# the SAME quote char (so a `\"` pair inside the content is reserved for the terminator, never
# content), then the closing backslash run and quote -- OR, if no such closing escape exists,
# end of line/string (fail closed, the same `(?:X|(?=[\r\n])|\Z)` shape round 3's own
# `_QUOTED_VALUE` fix above already uses). Pre-review (L2) reproduced the leak without this: an
# escaped-open value truncated mid-line or at end of input (a cut-off log line: `{\"state\": \"V`,
# no later `\"`) made the WHOLE alternative fail to match at all, so `_QUOTED_SECRET.match`
# returned None, `_QueryParamRule.sub` skipped the match entirely (key included), and the value
# printed raw -- this round's own new code, not a pre-existing gap. The two bare-quote
# alternatives (kept last, adjacent, and byte-for-byte unchanged from round 2) are the fallback for
# a value whose opening quote is NOT escaped; they cannot match a backslash as their first
# character either -- the same disclosed limitation `_QUOTED_VALUE` already has.
_QUOTED_SECRET = re.compile(
    r'(?<!\\)\\++"(?:[^"\\\r\n]++|\\++(?!"))*+(?:\\+"|(?=[\r\n])|\Z)'
    r"""|(?<!\\)\\++'(?:[^'\\\r\n]++|\\++(?!'))*+(?:\\+'|(?=[\r\n])|\Z)"""
    r'|"(?:[^"\\\r\n]|(?:\\\\)++(?:\\[^\r\n])?|\\[^\r\n])*+"?'
    r"""|'(?:[^'\\\r\n]|(?:\\\\)++(?:\\[^\r\n])?|\\[^\r\n])*+'?"""
)
# `[REDACTED]` is consumed whole (with anything glued to it) so the rule is idempotent: running it
# over its own output (the logging sink re-redacts the formatted line) never grows the marker.
_QUERY_VALUE = re.compile(r"""\[REDACTED\][^\s&,;"'}\])]*+|[^\s&,;"'}\])]++""")
_NAME_WORD_SPLIT = re.compile(r"[_.\-]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# Round 4 (QP-GLUED-VOCAB, QP-CASE-SPLIT-SEPARATOR, QP-SEPSTRIP): deny by default. A name that
# is not caught by its last word (above) is read with every separator stripped and lowercased;
# if that run ENDS in a credential name (`mfacode`, `smscode`, `signingkey`, `connectsid`,
# `recoverycode`, `hmacsig`, `aUtH_cOdE`, `sEsSiOn.KeY`, `co.de`) it is redacted, whatever the
# prefix -- the old closed vocabulary of credential prefixes leaked every prefix it did not list.
# The only way out is `_BENIGN_NAMES`, a CLOSED list of ordinary words that merely end in a
# credential name; it is matched against the whole stripped name, so it never excuses a
# credential prefix (`monkeysession` is still redacted) and a separator-split `mon_key` /
# camelCase `monKey` is decided by the last-word rule above, not by this list.
_NAME_SUFFIXES = tuple(sorted(_QUERY_PARAM_NAMES))
_BENIGN_NAMES = frozenset(
    {
        "monkey", "turkey", "hockey", "jockey", "donkey", "whiskey", "lackey",
        "decode", "encode", "barcode", "qrcode", "zipcode", "postcode", "geocode", "unicode",
        "opcode", "statuscode", "errorcode", "exitcode", "returncode",
        "estate", "realestate", "upstate", "restate",
        "compass", "bypass", "overpass", "underpass", "trespass", "surpass",
        "obsession", "possession", "concession", "profession", "procession", "recession",
        "succession",
    }
)  # fmt: skip


def _is_query_param_name(name: str) -> bool:
    # Round 5 (QP-CASE-SPLIT-SEGMENT): check each separator-delimited segment lowercased
    # BEFORE the camel split below. The camel split inserts a boundary at every
    # lowercase-to-uppercase transition, which can fall *inside* a random-case spelling of a
    # listed word that already sits in its own `_ - .`-delimited segment (`mon_KeY` -> the
    # `e`-`Y` transition in "KeY" splits it into "Ke"+"Y", so the last-word check below never
    # sees "key"; `status_CoDe` splits "CoDe" into "Co"+"De"). A segment produced by an actual
    # separator is never split further by case here -- it is lowered and compared whole.
    raw_segments = [w for w in _NAME_WORD_SPLIT.split(name) if w]
    if raw_segments and raw_segments[-1].lower() in _QUERY_PARAM_NAMES:
        return True
    words = [w for w in _NAME_WORD_SPLIT.split(_CAMEL_BOUNDARY.sub("_", name)) if w]
    if words and words[-1].lower() in _QUERY_PARAM_NAMES:
        return True
    stripped = _NAME_WORD_SPLIT.sub("", name).lower()
    return stripped.endswith(_NAME_SUFFIXES) and stripped not in _BENIGN_NAMES


class _QueryParamRule:
    """Duck-types `re.Pattern.sub(repl, text)` for `_RULES` (the replacement is built in)."""

    def sub(self, _repl: object, text: str) -> str:
        out: list[str] = []
        pos = 0
        for match in _QUERY_ASSIGN.finditer(text):
            if match.start() < pos or not _is_query_param_name(match.group("key")):
                continue
            if match.group("qsep") is not None:
                value = _QUOTED_SECRET.match(text, match.end())
            else:
                value = _QUERY_VALUE.match(text, match.end())
            if value is None:
                continue
            out.append(text[pos : match.end()])
            out.append(REDACTED)
            pos = value.end()
        out.append(text[pos:])
        return "".join(out)


_QUERY_SECRET_PARAM = _QueryParamRule()
_BASE64_RUN = re.compile(
    r"(?<![A-Za-z0-9+/_\-])(?P<body>[A-Za-z0-9+/_\-]{16,})={0,2}(?![A-Za-z0-9+/_=\-])"
)

# (name, compiled rule). The replacement for a rule is `_REPLACEMENTS[name]`
# (default: the bare marker). Rules run in this order: whole-line header values
# first, so a `Bearer x` inside one is gone before the key=value rule sees it.
_RULES: tuple[tuple[str, Any], ...] = (
    ("pem", _PEM),
    ("header", _HEADER),
    ("pair", _PAIR),
    ("bearer", _BEARER),
    ("prefix", _PREFIXES),
    ("jwt", _JWT_RUN),
    ("userinfo", _USERINFO),
    ("key_value", _KEY_VALUE),
    ("query_param", _QUERY_SECRET_PARAM),
)
_REPLACEMENTS: Mapping[str, str | Callable[[re.Match[str]], str]] = {
    "jwt": _redact_jwts_in_run,
    "header": rf"\g<key>\g<sep>{REDACTED}",
    "pair": rf"\g<pre>{REDACTED}",
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


_PERCENT_IN_TOKEN = re.compile(r"%[0-9A-Fa-f]{2}")
_NON_SPACE_RUN = re.compile(r"\S+")
_MAX_CUT_ROUNDS = 16


def _reveals_secret(segment: str) -> bool:
    """True if any percent-decoded layer (up to three) of `segment` contains a secret shape."""
    layer = segment
    for _ in range(_MAX_DECODE_LAYERS):
        layer = _normalise(unquote(layer))  # a decoded fullwidth `=` is `=` once NFKC'd (F1)
        if _scan(layer, _BASE64_LAYERS) != layer:
            return True
    return False


def _percent_token_starts(line: str) -> list[tuple[int, int | None]]:
    """(start, widened_start) for every maximal non-whitespace run that itself contains a
    percent-encoded byte (`%XX`) -- the same set of runs the old `\\S*%[0-9A-Fa-f]{2}` scan
    found, just linear: splitting on whitespace once and searching each disjoint run, instead of
    backtracking a greedy `\\S*` at every character position (quadratic on a long run with no
    closing hex pair, e.g. `"a" * n + "%"`). `widened_start` is the start of the run immediately
    before it, or None -- a key name separated from its encoded separator by whitespace
    (`token %3D <secret>`) is otherwise invisible to `_reveals_secret`, which only ever looks
    forward from a given start."""
    runs = list(_NON_SPACE_RUN.finditer(line))
    return [
        (run.start(), runs[i - 1].start() if i > 0 else None)
        for i, run in enumerate(runs)
        if _PERCENT_IN_TOKEN.search(run.group())
    ]


def _reveals_secret_at(line: str, start: int, widened: int | None) -> bool:
    """Whether decoding from `start` reveals a secret, also trying the widened (one word
    earlier) window when the narrow one alone does not -- see `_percent_token_starts`."""
    return _reveals_secret(line[start:]) or (
        widened is not None and _reveals_secret(line[widened:])
    )


def _cut_encoded_secrets(line: str) -> str:
    """Fail closed on a percent-encoded secret the raw scan could not see. The decoded form is
    used only to *detect*: the raw text from the earliest still-revealing token to the end of the
    line is replaced, so no decoded fragment is ever returned and no encoded one is kept."""
    for _ in range(_MAX_CUT_ROUNDS):
        candidates = _percent_token_starts(line)
        if not candidates or not _reveals_secret_at(line, *candidates[0]):
            return line
        low, high = 0, len(candidates) - 1  # candidates[low] reveals; find the last one that does
        while low < high:
            mid = (low + high + 1) // 2
            if _reveals_secret_at(line, *candidates[mid]):
                low = mid
            else:
                high = mid - 1
        line = line[: candidates[low][0]] + REDACTED
    candidates = _percent_token_starts(line)  # still revealing after the cap: fail closed
    return line if not candidates else line[: candidates[0][0]] + REDACTED


def _redact_encoded(text: str) -> str:
    if "%" not in text:
        return text
    return "".join(
        _cut_encoded_secrets(body) + ending
        for body, ending in re.findall(r"([^\r\n]*)(\r\n|\r|\n|\Z)", text)
        if body or ending
    )


def _redact(text: str) -> str:
    if len(text) > MAX_TEXT_LENGTH:
        raise LogRedactionError
    return _redact_encoded(_scan(_normalise(text), _BASE64_LAYERS))


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


def _key_text(key: object) -> str:
    """A mapping key or header name as redacted text (bytes names are decoded, not repr'd)."""
    if isinstance(key, bytes | bytearray):
        return redact_text(bytes(key).decode("utf-8", "replace"))
    return redact_text(str.__str__(key) if isinstance(key, str) else repr(key))


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
            name = _key_text(key)
            if _SECRET_KEY_NAME.fullmatch(name):
                out[name] = REDACTED
            else:
                out[name] = _redact_value(item, depth + 1, path)
        return out
    if isinstance(value, list | tuple):
        if len(value) == 2 and isinstance(value[0], str | bytes | bytearray):
            name = _key_text(value[0])  # an ASGI-style (name, value) header pair
            if _SECRET_KEY_NAME.fullmatch(name):
                pair = [name, REDACTED]
                return tuple(pair) if isinstance(value, tuple) else pair
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


def _wrap_make_record(
    original: Callable[..., logging.LogRecord],
) -> Callable[..., logging.LogRecord]:
    """`Logger.makeRecord` attaches `extra=` *after* the record factory returns, so the factory
    alone never sees it. Scrubbing here covers every logger, whatever its handlers."""

    def make_record(self: logging.Logger, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(self, *args, **kwargs)
        _scrub_record(record)
        return record

    make_record.__wrapped__ = original  # type: ignore[attr-defined]
    make_record._ftf0904_redacting = True  # type: ignore[attr-defined]
    return make_record


def install_redaction(root: logging.Logger | None = None) -> None:
    """Ingress + sink for `root` (default: the root logger). Idempotent."""
    current = logging.getLogRecordFactory()
    if not getattr(current, "_ftf0904_redacting", False):
        logging.setLogRecordFactory(_wrap_factory(current))
    maker = logging.Logger.makeRecord
    if not getattr(maker, "_ftf0904_redacting", False):
        logging.Logger.makeRecord = _wrap_make_record(maker)  # type: ignore[method-assign]
    for handler in (root or logging.getLogger()).handlers:
        attach_sink(handler)
