"""Normalized duplicate detector (FTF-0512, Section 8.1 step 11/10/67/77/81).

Section 8.1's import pipeline step 11 ("Detect normalized duplicates") is
distinct from step 10 ("Detect exact duplicate records", FTF-0511's own
`detect_exact_duplicates`, covering Section 10 methods 1-2: raw SHA-256 and
canonical-JSON hash). This module covers Section 10 method 3 ("Text
normalization match": lowercase, whitespace normalization, punctuation
normalization, remove generated IDs/timestamps using schema-aware rules) --
catching records that are the same conversation with only cosmetic
formatting differences that FTF-0511's byte-exact/canonical-JSON hashes
would never collide on (different case, extra whitespace, different
punctuation, or a different generated trace ID/timestamp embedded in the
text). Method 3's own severity, Section 10's literal table, has no
cross-purpose carve-out ("normalized match: always block", unlike the raw
hash row's "always block across train vs held-out"), so every group this
module finds is unconditionally blocking -- see `DuplicateGroup.is_blocking`.

This task's own `database_changes` clause ("No independent schema change.
Consume frozen contract.") fixes this module's shape, the same as
FTF-0506/FTF-0507/FTF-0508/FTF-0511: a pure, synchronous, no-I/O, no-DB
function operating on FTF-0506's own canonical `content` body shape
(`{"messages": [...], "metadata": {...}}`, the same shape FTF-0508's own
`validate_record_batch` already validates on every Section 6.11
`DatasetRecord` candidate).

This task's own acceptance clause ("Canonicalized hash matching preserves
meaningful tool/ID semantics while detecting formatting duplicates") is met
by `compute_normalized_text_hash`'s own asymmetric treatment of message
content, applied uniformly by `_normalize_message`:

  - **detecting formatting duplicates**: only `{"type": "text", ...}`
    content parts are normalized -- lowercased, generated-ID/timestamp
    patterns redacted, punctuation stripped, whitespace collapsed
    (`_normalize_text`). Two records whose text differs only in case,
    punctuation, whitespace, or an embedded generated trace ID/timestamp
    hash identically.
  - **preserves meaningful tool/ID semantics**: `{"type": "json", ...}`
    content parts (a tool result's structured outcome) and every
    `tool_calls` entry's `id`/`name`/`arguments` are copied into the
    hashed document byte-for-byte, never passed through `_normalize_text`.
    A tool call or a JSON tool result carries real business data (a
    refund amount, an order status, a lookup result) that Section 10
    method 3's own "text normalization" scope was never meant to touch --
    this module reads that scope literally.

This task's own negative_acceptance ("Normalization cannot collapse
distinct customer outcomes or strip relevant security constraints") is a
direct consequence of the same design, made independently testable:

  - **cannot collapse distinct customer outcomes**: since `json` content
    parts and every tool call's `arguments` are preserved verbatim, two
    records whose only difference is a tool argument or a JSON tool
    result's value (a different order ID, a different refund amount, a
    different approval outcome) never hash the same, however identical
    their surrounding prose is after normalization.
  - **cannot strip relevant security constraints**: `_normalize_text`'s
    generated-ID/timestamp redaction is deliberately narrow -- only
    syntactically self-identifying generated shapes (an RFC 4122 UUID, an
    ISO-8601 timestamp) are recognized and replaced with a fixed
    placeholder. It never touches ordinary words, so two records whose
    text differs by a real word (for example "refunds are forbidden" vs.
    "refunds are allowed") never collide; and it never touches a
    business-meaningful identifier that does not carry a generated
    shape (for example a plain order number embedded in prose), so two
    records naming different customers/orders in otherwise-identical
    prose never collide either.

Every candidate's own `content` shape is validated before hashing
(`compute_normalized_text_hash`, reusing the same "valid role ordering" /
"no unsupported content type" / "no null role/content ambiguity"
vocabulary FTF-0506's own `normalize_conversation` already established, on
the *already-normalized* Section 7 shape rather than a raw import).
Never raises on a per-row violation -- `detect_normalized_duplicates`
reports a malformed `content`, a missing/unrecognized `purpose`, or a
non-object candidate as an `NormalizedDuplicateIssue` with row evidence,
the same "never raise on a per-row violation, always report" discipline
FTF-0508's/FTF-0511's own reports already established. Only a batch that
is not a real sequence of records at all (not a sequence, or empty) raises
`NormalizedDuplicateDetectionError` outright -- there is no row to attach
that failure's evidence to.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
FTF-0511 ("no live caller yet"): this task's own packet `files` list names
only this module and its test, not an importer/route. No dataset-import
pipeline route exists yet in this repository to call
`detect_normalized_duplicates` (Section 67.1's actual import pipeline
routes are separate, later, dependency-gated tasks). `proving_symbol`
stays blank; `product_truth_check.py`'s product-true count is deliberately
unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0511: the packet names
`services/training-worker/data/normalized_duplicate_detector.py`, but this
repo's real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the
real data/review worker root -- there is no `services/` tree in this
repository. Placed alongside FTF-0506's/FTF-0507's/FTF-0508's/FTF-0511's
own modules instead of creating a second, non-existent tree.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "SUPPORTED_PURPOSES",
    "DuplicateGroup",
    "NormalizedDuplicateDetectionError",
    "NormalizedDuplicateIssue",
    "NormalizedDuplicateReport",
    "compute_normalized_text_hash",
    "detect_normalized_duplicates",
]

# Section 6.8F's own frozen `DatasetSourceConnection.allowed_purposes_json`
# vocabulary (`apps/api/app/datasets/dataset_source_connection_schema.py`'s
# own `ALLOWED_PURPOSES`), mirrored here rather than imported -- same
# cross-boundary precedent FTF-0511 already established (`workers/data`
# cannot import `apps/api`).
SUPPORTED_PURPOSES = frozenset(
    {"training", "validation", "held_out_evaluation", "candidate_development", "baseline_only"}
)

_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
_ALLOWED_CONTENT_TYPES = frozenset({"text", "json"})
_REQUIRED_CONTENT_KEYS = frozenset({"messages", "metadata"})

# Section 10 method 3: "remove generated IDs/timestamps using schema-aware
# rules". Deliberately narrow -- only syntactically self-identifying
# generated shapes are recognized (an RFC 4122 UUID; an ISO-8601
# timestamp), never a generic numeric-looking business identifier (an
# order number, an account ID) a real customer outcome might depend on.
# See this module's own negative_acceptance discussion above.
_UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_ISO_TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:\d{2})?\b"
)
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")

# FTF-0512-F1 round 2: NFKC's compatibility decomposition is not limited to
# look-alike letters -- it also rewrites a superscript/subscript digit, a
# vulgar fraction or a circled/Roman numeral (Unicode categories No/Nl) into
# plain ASCII digits, which can then fuse with a neighbouring real digit run
# ("10⁵ requests" -> "105 requests", "x²" -> "x2", "1¼" ->
# "11/4" after punctuation-stripping the fraction slash). That is exactly the
# "cannot collapse distinct customer outcomes" negative_acceptance clause --
# a mathematical/typographic notation is not a formatting variant of a plain
# number. No/Nl characters are therefore never NFKC-folded: they are kept
# byte-for-byte as their own distinct symbol (never equal to any ASCII digit
# sequence, and never equal to a different No/Nl look-alike either).
_DIGIT_LIKE_CATEGORIES = frozenset({"No", "Nl"})

# FTF-0512-F1 round 2: Unicode canonical reordering sorts a maximal run of
# non-starter (combining, ccc != 0) code points before composition/
# decomposition can proceed; an unbounded run is an algorithmic-complexity
# risk (a verifier reproduced a 2.04 MB record, mostly alternating
# combining marks, taking 99.7s to normalize -- Law 17's "provider
# failure/unknown state stays visible", applied here to a resource-exhaustion
# shape rather than an outright crash). UAX #15's own "Stream-Safe Text
# Format" bounds any meaningful combining sequence to 30 non-starters;
# anything longer is refused outright (a typed per-row issue, not a crash or
# a silent truncation) rather than processed at unbounded cost.
_MAX_COMBINING_RUN = 30

# FTF-0512-F1 round 2: the normalization semantics above change what two
# records hash as equal/distinct. Mixed into the hashed document so any
# already-persisted digest (FTF-0515's evidence_digest, FTF-0516-P1's
# issue_id) is never silently reinterpreted under a different rule --
# bump this whenever `_normalize_text`'s behaviour changes again.
# FTF-0512-F1 round 3: 3 -- NFKC compatibility mappings that would fuse
# alphanumerics into their neighbours are now separated (`_fold_action`).
# FTF-0512-F2: 4 -- Unicode symbols (Sm/Sc/Sk/So) are kept as their own
# tokens instead of being stripped like punctuation (`_strip_punctuation`).
_DETECTOR_VERSION = 4


class NormalizedDuplicateDetectionError(ValueError):
    """The candidate record batch itself is not a real sequence of
    records -- not a sequence at all, or empty. There is no row to attach
    this failure's evidence to, so it is raised, not reported as a row
    issue. Also raised internally by `compute_normalized_text_hash` on a
    malformed `content` document -- `detect_normalized_duplicates` catches
    that case per-row and reports it as an issue instead of propagating it."""


@dataclass(frozen=True)
class NormalizedDuplicateIssue:
    batch_position: int
    record_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    batch_positions: tuple[int, ...]
    record_indices: tuple[int | None, ...]
    purposes: frozenset[str]
    is_cross_purpose: bool

    @property
    def is_blocking(self) -> bool:
        # Section 10's own literal severity table: "normalized match:
        # always block" -- no cross-purpose carve-out, unlike the raw-hash
        # row FTF-0511 already applies its own conditional rule to.
        return True


@dataclass(frozen=True)
class NormalizedDuplicateReport:
    duplicate_groups: tuple[DuplicateGroup, ...]
    issues: tuple[NormalizedDuplicateIssue, ...]

    @property
    def blocking_groups(self) -> tuple[DuplicateGroup, ...]:
        return tuple(group for group in self.duplicate_groups if group.is_blocking)

    @property
    def is_clean(self) -> bool:
        return not self.issues and not self.blocking_groups


def _strip_format_characters(text: str) -> str:
    # Unicode category Cf (zero-width space/joiner/non-joiner, word joiner,
    # BOM, soft hyphen, bidi controls, ...): invisible, so two texts that
    # differ only in them are the same text to a reader (FTF-0512-F1).
    return "".join(char for char in text if unicodedata.category(char) != "Cf")


@lru_cache(maxsize=65536)
def _nfkd_non_starter_flags(char: str) -> tuple[bool, ...]:
    # UAX #15 Stream-Safe Text Format counts non-starters (ccc != 0) in the NFKD
    # decomposition, not in the input: U+0F73 has ccc 0 but decomposes to two
    # non-starters (U+0F71 U+0F72), and the quadratic reordering cost is paid on
    # the decomposed form. NFKD of one code point is context-free (no
    # composition), so per-character flags concatenate exactly.
    return tuple(unicodedata.combining(part) != 0 for part in unicodedata.normalize("NFKD", char))


def _assert_safe_combining_run(text: str) -> None:
    run_length = 0
    for char in text:
        for is_non_starter in _nfkd_non_starter_flags(char):
            if is_non_starter:
                run_length += 1
                if run_length > _MAX_COMBINING_RUN:
                    raise NormalizedDuplicateDetectionError(
                        "text content part has a run of more than "
                        f"{_MAX_COMBINING_RUN} NFKD non-starters (UAX #15 Stream-Safe Text "
                        "Format bound) -- refused rather than normalized at unbounded cost"
                    )
            else:
                run_length = 0


# FTF-0512-F1 round 3: an NFKC compatibility mapping that only changes the
# glyph form of the SAME character (fullwidth, math/font styles, circled
# letters, Arabic positional forms, halfwidth, vertical) is a look-alike and is
# folded. Every other compatibility mapping that yields alphanumerics (a
# superscript/subscript, a fraction, a squared CJK/telegraph/unit symbol, a
# non-letter `<compat>` symbol such as a unit or currency abbreviation) writes
# DIFFERENT characters than the one it replaces, and folding it in place would
# fuse them into the neighbouring digits/letters ("2㏠" -> "21日", "2ⁿ" -> "2n",
# a false blocking finding between two different records). Those are folded
# AS A SEPARATED TOKEN instead, so the neighbours can never fuse with the result.
_SAME_CHARACTER_TAGS = frozenset(
    {
        "<wide>",
        "<narrow>",
        "<font>",
        "<circle>",
        "<initial>",
        "<medial>",
        "<final>",
        "<isolated>",
        "<vertical>",
        "<small>",
    }
)

_FOLD_KEEP = 0  # folded in place with its neighbours
_FOLD_ISOLATE_VERBATIM = 1  # No/Nl: kept byte-for-byte, never folded
_FOLD_SEPARATE = 2  # compatibility mapping that would fuse: folded as its own token


_DECOMPOSITION_TAG = re.compile(r"<[^>]+>")


def _decomposition_tag(char: str) -> str:
    match = _DECOMPOSITION_TAG.match(unicodedata.decomposition(char))
    return match.group() if match else ""


@lru_cache(maxsize=65536)
def _fold_action(char: str) -> int:
    if unicodedata.category(char) in _DIGIT_LIKE_CATEGORIES:
        return _FOLD_ISOLATE_VERBATIM
    if unicodedata.category(char).startswith("S") and any(
        unicodedata.category(part).startswith("M") for part in unicodedata.normalize("NFKC", char)
    ):
        # FTF-0512-F2: a spacing diacritic symbol (Sk, e.g. U+00A8 DIAERESIS)
        # folds to SPACE + a combining mark, which the punctuation rule would
        # then strip as non-word -- the symbol would vanish. Keep it verbatim.
        return _FOLD_ISOLATE_VERBATIM
    if not _decomposition_tag(char) or _decomposition_tag(char) in _SAME_CHARACTER_TAGS:
        return _FOLD_KEEP
    if _decomposition_tag(char) == "<compat>" and unicodedata.category(char).startswith("L"):
        # ligatures and digraphs (ﬁ, ǆ, ĳ, µ): the same letters, spelled differently
        return _FOLD_KEEP
    return _FOLD_SEPARATE


def _fold_look_alikes(text: str) -> str:
    # FTF-0512-F1 round 2/3: NFKC-fold everything EXCEPT the characters
    # `_fold_action` isolates, which are segmented out of the string being
    # folded so they can never compose or merge with a neighbouring character.
    # A character of its own (not a combining mark) already breaks any
    # composition/decomposition sequence that would otherwise cross it, so
    # isolating it like this changes nothing about how the segments on either
    # side fold.
    segments: list[str] = []
    pending: list[str] = []
    for char in text:
        action = _fold_action(char)
        if action == _FOLD_KEEP:
            pending.append(char)
            continue
        if pending:
            segments.append(unicodedata.normalize("NFKC", "".join(pending)))
            pending = []
        if action == _FOLD_ISOLATE_VERBATIM:
            segments.append(char)
        else:
            segments.append(" " + unicodedata.normalize("NFKC", char) + " ")
    if pending:
        segments.append(unicodedata.normalize("NFKC", "".join(pending)))
    return "".join(segments)


def _lower_case(text: str) -> str:
    return text.lower()


def _punctuation_replacement(match: re.Match[str]) -> str:
    # Section 10 method 3 says punctuation normalization: Unicode P* is
    # stripped. A math/currency/modifier/other symbol (Sm/Sc/Sk/So) carries
    # meaning ("x <= 5" vs "x >= 5", "$100" vs "EUR100"), so it is kept as its own
    # space-separated token instead -- FTF-0512's "cannot collapse distinct
    # customer outcomes" negative_acceptance clause.
    char = match.group()
    return f" {char} " if unicodedata.category(char).startswith("S") else " "


def _strip_punctuation(text: str) -> str:
    return _PUNCTUATION_PATTERN.sub(_punctuation_replacement, text)


def _finish_text(folded: str) -> str:
    lowered = _lower_case(folded)
    # Generated IDs/timestamps are REMOVED, not replaced by a placeholder:
    # Section 10 method 3 says "remove", and a placeholder word would keep a
    # record that carries a timestamp distinct from the same text without
    # one (the one-sided case) while colliding with a text that literally
    # says "generated timestamp".
    redacted = _ISO_TIMESTAMP_PATTERN.sub(" ", lowered)
    redacted = _UUID_PATTERN.sub(" ", redacted)
    return _WHITESPACE_PATTERN.sub(" ", _strip_punctuation(redacted)).strip()


def _normalize_text(text: str) -> str:
    # FTF-0512-F1: fold look-alikes first, so every later rule sees one
    # canonical spelling. Cf is stripped BEFORE the combining-run bound and
    # NFKC so an invisible character can neither split a compatibility/
    # composition sequence nor reset the run count; NFKC never produces or
    # alters a Cf character (checked over all of Unicode), so one pass is enough.
    stripped = _strip_format_characters(text)
    _assert_safe_combining_run(stripped)
    return _finish_text(_fold_look_alikes(stripped))


def _normalize_content_part(part: object, *, field: str) -> dict[str, object]:
    if not isinstance(part, Mapping):
        raise NormalizedDuplicateDetectionError(f"{field}: content part must be an object")
    part_type = part.get("type")
    if part_type not in _ALLOWED_CONTENT_TYPES:
        raise NormalizedDuplicateDetectionError(f"{field}: unsupported content type {part_type!r}")

    if part_type == "text":
        text = part.get("text")
        if not isinstance(text, str):
            raise NormalizedDuplicateDetectionError(
                f"{field}: text content part must carry a string 'text'"
            )
        # Only the text surface form is normalized here -- this is where
        # "detecting formatting duplicates" comes from.
        return {"type": "text", "text": _normalize_text(text)}

    # part_type == "json": preserved byte-for-byte. A json content part
    # carries a structured business outcome (a tool result's status, an
    # evaluation grade, ...); Section 10 method 3 normalizes *text*
    # formatting, not business data. negative_acceptance ("cannot collapse
    # distinct customer outcomes") is met by never touching this value.
    if "value" not in part:
        raise NormalizedDuplicateDetectionError(f"{field}: json content part missing 'value'")
    try:
        json.dumps(part["value"])
    except (TypeError, ValueError) as exc:
        raise NormalizedDuplicateDetectionError(
            f"{field}: json content part value is not JSON-serializable: {exc}"
        ) from exc
    return {"type": "json", "value": part["value"]}


def _normalize_tool_call(call: object, *, field: str) -> dict[str, object]:
    if not isinstance(call, Mapping):
        raise NormalizedDuplicateDetectionError(f"{field}: tool call must be an object")
    call_id = call.get("id")
    name = call.get("name")
    arguments = call.get("arguments")
    if not isinstance(call_id, str) or not call_id:
        raise NormalizedDuplicateDetectionError(f"{field}: tool call id must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise NormalizedDuplicateDetectionError(
            f"{field}: tool call name must be a non-empty string"
        )
    if not isinstance(arguments, Mapping):
        raise NormalizedDuplicateDetectionError(f"{field}: tool call arguments must be an object")
    try:
        json.dumps(dict(arguments))
    except (TypeError, ValueError) as exc:
        raise NormalizedDuplicateDetectionError(
            f"{field}: tool call arguments are not JSON-serializable: {exc}"
        ) from exc
    # id/name/arguments preserved byte-for-byte -- the "preserves
    # meaningful tool/ID semantics" half of this task's own acceptance
    # clause. A tool call names a real business action (a refund amount,
    # an order ID); text-formatting normalization never touches it.
    return {"id": call_id, "name": name, "arguments": dict(arguments)}


def _normalize_message(message: object, *, field: str) -> dict[str, object]:
    if not isinstance(message, Mapping):
        raise NormalizedDuplicateDetectionError(f"{field}: message must be an object")

    role = message.get("role")
    if role not in _ALLOWED_ROLES:
        raise NormalizedDuplicateDetectionError(f"{field}: unmapped role {role!r}")

    raw_content = message.get("content")
    if not isinstance(raw_content, list):
        raise NormalizedDuplicateDetectionError(f"{field}: content must be a list")

    normalized: dict[str, object] = {
        "role": role,
        "content": [
            _normalize_content_part(part, field=f"{field}.content[{index}]")
            for index, part in enumerate(raw_content)
        ],
    }

    raw_tool_calls = message.get("tool_calls")
    if raw_tool_calls is not None:
        if not isinstance(raw_tool_calls, list):
            raise NormalizedDuplicateDetectionError(f"{field}: tool_calls must be a list")
        normalized["tool_calls"] = [
            _normalize_tool_call(call, field=f"{field}.tool_calls[{index}]")
            for index, call in enumerate(raw_tool_calls)
        ]

    tool_call_id = message.get("tool_call_id")
    if tool_call_id is not None:
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise NormalizedDuplicateDetectionError(
                f"{field}: tool_call_id must be a non-empty string"
            )
        normalized["tool_call_id"] = tool_call_id

    name = message.get("name")
    if name is not None:
        if not isinstance(name, str) or not name:
            raise NormalizedDuplicateDetectionError(f"{field}: name must be a non-empty string")
        normalized["name"] = name

    return normalized


def _digest_document(normalized_messages: list[dict[str, object]]) -> dict[str, object]:
    return {"detector_version": _DETECTOR_VERSION, "messages": normalized_messages}


def compute_normalized_text_hash(content: object) -> str:
    """Computes Section 10 method 3's own "text normalization match" hash
    over one already-canonical `content` document (`{"messages": [...],
    "metadata": {...}}`, FTF-0506's own canonical body shape). Message
    `role` structure, `json` content parts and every tool call's
    `id`/`name`/`arguments` are preserved byte-for-byte; only `text`
    content parts are lowercased, whitespace-normalized,
    punctuation-normalized and generated-ID/timestamp-redacted. `metadata`
    is deliberately excluded -- it describes the import, not the
    conversation content two records are being compared for. Raises
    `NormalizedDuplicateDetectionError` on any shape this function cannot
    trust; never guesses a lossy reading of a document it does not
    recognize."""
    if not isinstance(content, Mapping) or set(content) != _REQUIRED_CONTENT_KEYS:
        raise NormalizedDuplicateDetectionError(
            "content must be an object with exactly the canonical 'messages'/'metadata' keys"
        )

    messages = content.get("messages")
    if not isinstance(messages, list) or not messages:
        raise NormalizedDuplicateDetectionError("content.messages must be a non-empty list")

    normalized_messages = [
        _normalize_message(message, field=f"messages[{index}]")
        for index, message in enumerate(messages)
    ]

    canonical = json.dumps(
        _digest_document(normalized_messages), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _ValidCandidate:
    batch_position: int
    record_index: int | None
    purpose: str
    digest: str


def detect_normalized_duplicates(
    records: Sequence[Mapping[str, object]],
) -> NormalizedDuplicateReport:
    """Detects Section 10 method 3 ("text normalization match") duplicate
    records in an already-normalized candidate batch. Every candidate must
    carry `content` (FTF-0506's own canonical body shape) and `purpose`
    (Section 6.8F's own closed vocabulary). Never raises on a per-row
    violation -- returns a `NormalizedDuplicateReport` whose `issues` carry
    row evidence. Raises `NormalizedDuplicateDetectionError` only when the
    batch itself is not a real sequence of records (not a sequence, or
    empty)."""
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise NormalizedDuplicateDetectionError("records must be a sequence of candidate records")
    if not records:
        raise NormalizedDuplicateDetectionError("records must be a non-empty sequence")

    issues: list[NormalizedDuplicateIssue] = []
    valid: list[_ValidCandidate] = []

    for batch_position, candidate in enumerate(records):
        if not isinstance(candidate, Mapping):
            issues.append(
                NormalizedDuplicateIssue(
                    batch_position=batch_position,
                    record_index=None,
                    code="invalid_candidate",
                    message="candidate record must be an object",
                )
            )
            continue

        raw_record_index = candidate.get("record_index")
        record_index: int | None
        if isinstance(raw_record_index, int) and not isinstance(raw_record_index, bool):
            record_index = raw_record_index
        else:
            record_index = None

        purpose = candidate.get("purpose")
        if purpose not in SUPPORTED_PURPOSES:
            issues.append(
                NormalizedDuplicateIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_purpose",
                    message=f"purpose {purpose!r} is not a recognized value",
                )
            )
            continue

        try:
            digest = compute_normalized_text_hash(candidate.get("content"))
        except NormalizedDuplicateDetectionError as exc:
            issues.append(
                NormalizedDuplicateIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="unknown_content",
                    message=str(exc),
                )
            )
            continue

        assert isinstance(purpose, str)
        valid.append(
            _ValidCandidate(
                batch_position=batch_position,
                record_index=record_index,
                purpose=purpose,
                digest=digest,
            )
        )

    by_digest: dict[str, list[_ValidCandidate]] = {}
    for valid_candidate in valid:
        by_digest.setdefault(valid_candidate.digest, []).append(valid_candidate)

    groups: list[DuplicateGroup] = []
    for digest in sorted(by_digest):
        members = by_digest[digest]
        if len(members) < 2:
            continue
        purposes = frozenset(member.purpose for member in members)
        groups.append(
            DuplicateGroup(
                digest=digest,
                batch_positions=tuple(member.batch_position for member in members),
                record_indices=tuple(member.record_index for member in members),
                purposes=purposes,
                is_cross_purpose=len(purposes) > 1,
            )
        )

    return NormalizedDuplicateReport(duplicate_groups=tuple(groups), issues=tuple(issues))
