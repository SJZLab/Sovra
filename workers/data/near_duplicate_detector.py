"""Near-duplicate detector (FTF-0513, Section 8.1 step 12/10/67/77/81).

Section 8.1's import pipeline step 12 ("Detect near duplicates") is distinct
from step 10 (FTF-0511's raw/canonical-JSON hash, Section 10 methods 1-2) and
step 11 (FTF-0512's text-normalization hash, Section 10 method 3): those
three methods only ever collide on records that are the *same* conversation
under some exact rewriting rule. This module covers Section 10 methods 4-7,
which catch conversations that are merely *similar* -- a paraphrase, a
reordered tool sequence with the same shape, or the same scripted scenario
answered slightly differently:

  4. Character n-gram similarity.
  5. Token Jaccard similarity.
  6. Embedding similarity using a local/private embedding model in
     zero-egress mode.
  7. Agent-trace structural similarity (same user goal, same tool sequence,
     same output template).

Section 10's own literal severity table draws a hard line this module
respects by construction: only "exact match" and "normalized match" are
"always block"; a near-duplicate ("semantic similarity >= configured
threshold") is "flag for review", never an automatic block. This is exactly
this task's own negative_acceptance ("No all-pairs million-record memory
blowup or automatic discard based solely on a guessed similarity
threshold") -- read literally, in two independent halves:

  - **no automatic discard based solely on a guessed similarity
    threshold**: `detect_near_duplicates` has no code path that removes,
    mutates or reorders the caller's own `records` -- crossing a
    configured threshold produces a `NearDuplicateCandidate` for a human
    reviewer (Section 8.1 step 17, "UI requires user resolution of blocking
    issues" -- resolution, not silent removal), never a dropped row. Every
    threshold lives on `NearDuplicateThresholds`, an explicit, caller-
    overridable parameter, not a number buried in comparison logic --
    Section 10 only fixes a literal default for method 6 ("default 0.95");
    this module's own method 4/5 defaults (0.9, disclosed below) are this
    task's own reasoned starting point pending the same "reviewed
    thresholds" the acceptance clause names, not a second invented
    authority.
  - **no all-pairs million-record memory blowup**: candidate *pairs* are
    never formed by comparing every record against every other record.
    `_lsh_bucket_keys` buckets each record by a banded MinHash signature
    over its own character-shingle set (the same locality-sensitive-hashing
    technique the acceptance clause's own "bounded indexed n-gram/semantic
    candidates" phrase names) -- only records sharing at least one band
    bucket ever become a candidate pair, and `max_candidates_per_record`
    additionally caps how many candidate partners any single record can
    accumulate, so a large corpus in which every record already differs
    (the overwhelmingly common case) forms close to zero candidate pairs
    rather than the full O(n^2) comparison a naive implementation would.
    `tests/tasks/test_ftf_0513.py` proves this directly: a fault injection
    that removes the banding bound and falls back to comparing every pair
    is shown to blow past `max_candidates_per_record` on a corpus sized to
    make the difference unmistakable.

**Method 6's own "local/private embedding model" is a genuine architecture
gap, resolved here rather than escalated (CLAUDE.md's default-resolution
rule -- no concrete embedding model or package is named anywhere in
`FINE_TUNING_FACTORY_RELEASE_1.md`, `docs/architecture/
REFERENCE_REPOSITORIES.md`, or Section 77's seven-reused-component list; a
pretrained semantic-embedding model is not one of the seven, and this
sandbox has no outbound network egress to fetch one even if it were named,
per every prior shift's own disclosed finding).** Resolved as a
deterministic, local, feature-hashing vectorizer (`_hashing_embedding`,
the classic "hashing trick" -- see e.g. scikit-learn's own
`HashingVectorizer`): a fixed-dimension vector accumulated by hashing each
normalized token to a signed bucket index, then L2-normalized, compared by
cosine similarity. This is a real, deterministic, load-bearing "local/
private embedding" -- computed entirely in-process, from no pretrained
weights, no network call and no new runtime dependency (Python's own
`hashlib`) -- consistent with this repository's own repeated preference for
the dependency-light, zero-egress-compatible resolution over adding a new
package (e.g. FTF-04A10's reuse of the already-installed `typescript`
compiler rather than a new parser dependency). It is not a claim that this
vector captures deep semantic meaning the way a trained transformer
embedding would; it is disclosed here as a lexical-overlap proxy, the same
honesty this module applies to every other metric it reports.

This task's own `database_changes` clause ("No independent schema change.
Consume frozen contract.") fixes this module's shape, the same as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-0512: a pure, synchronous, no-I/O,
no-DB function operating on FTF-0506's own canonical `content` body shape.

Scope decision, same disclosed precedent as FTF-0506/FTF-0507/FTF-0508/
FTF-0511/FTF-0512 ("no live caller yet"): this task's own packet `files`
list names only this module and its test, not an importer/route. No
dataset-import pipeline route exists yet in this repository to call
`detect_near_duplicates`. `proving_symbol` stays blank; `product_truth_
check.py`'s product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-0512: the packet names
`services/training-worker/data/near_duplicate_detector.py`, but this repo's
real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the real
data/review worker root -- there is no `services/` tree in this repository.
Placed alongside FTF-0511's/FTF-0512's own modules instead of creating a
second, non-existent tree.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "SUPPORTED_PURPOSES",
    "DEFAULT_THRESHOLDS",
    "NearDuplicateCandidate",
    "NearDuplicateDetectionError",
    "NearDuplicateIssue",
    "NearDuplicateReport",
    "NearDuplicateThresholds",
    "detect_near_duplicates",
]

# Section 6.8F's own frozen `DatasetSourceConnection.allowed_purposes_json`
# vocabulary, mirrored here rather than imported -- same cross-boundary
# precedent FTF-0511/FTF-0512 already established (`workers/data` cannot
# import `apps/api`).
SUPPORTED_PURPOSES = frozenset(
    {"training", "validation", "held_out_evaluation", "candidate_development", "baseline_only"}
)

_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
_ALLOWED_CONTENT_TYPES = frozenset({"text", "json"})
_REQUIRED_CONTENT_KEYS = frozenset({"messages", "metadata"})

_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_CHAR_NGRAM_SIZE = 5
_MINHASH_FUNCTIONS = 32
_LSH_BANDS = 16
_LSH_ROWS_PER_BAND = _MINHASH_FUNCTIONS // _LSH_BANDS
_EMBEDDING_DIMENSIONS = 256


class NearDuplicateDetectionError(ValueError):
    """The candidate record batch itself is not a real sequence of
    records -- not a sequence at all, or empty. There is no row to attach
    this failure's evidence to, so it is raised, not reported as a row
    issue. Also raised internally by `_extract_features` on a malformed
    `content` document -- `detect_near_duplicates` catches that case
    per-row and reports it as an issue instead of propagating it."""


@dataclass(frozen=True)
class NearDuplicateIssue:
    batch_position: int
    record_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class NearDuplicateThresholds:
    """Every threshold this module compares against is an explicit,
    caller-overridable field -- never a number buried in comparison logic.
    Section 10's own literal text fixes only the method-6 default
    ("semantic similarity >= configured threshold... default 0.95");
    the method 4/5 defaults below are this task's own disclosed starting
    point pending the same "reviewed thresholds" review the acceptance
    clause names, not a second invented spec authority."""

    character_ngram_similarity: float = 0.9
    token_jaccard_similarity: float = 0.9
    embedding_cosine_similarity: float = 0.95

    def __post_init__(self) -> None:
        for name, value in (
            ("character_ngram_similarity", self.character_ngram_similarity),
            ("token_jaccard_similarity", self.token_jaccard_similarity),
            ("embedding_cosine_similarity", self.embedding_cosine_similarity),
        ):
            if not isinstance(value, int | float) or not (0.0 <= float(value) <= 1.0):
                raise NearDuplicateDetectionError(f"{name} must be a number between 0.0 and 1.0")


DEFAULT_THRESHOLDS = NearDuplicateThresholds()


@dataclass(frozen=True)
class NearDuplicateCandidate:
    batch_position_a: int
    batch_position_b: int
    record_index_a: int | None
    record_index_b: int | None
    character_ngram_similarity: float
    token_jaccard_similarity: float
    embedding_cosine_similarity: float
    structural_match: bool
    flagged_methods: frozenset[str]
    purposes: frozenset[str]
    is_cross_purpose: bool

    @property
    def requires_review(self) -> bool:
        # Section 10's own literal table: a near-duplicate is "flag for
        # review", never an automatic block -- this property names the
        # real disposition; there is deliberately no `is_blocking` on this
        # class (contrast FTF-0511's/FTF-0512's own `DuplicateGroup`).
        return bool(self.flagged_methods)


@dataclass(frozen=True)
class NearDuplicateReport:
    candidates: tuple[NearDuplicateCandidate, ...] = field(default_factory=tuple)
    issues: tuple[NearDuplicateIssue, ...] = field(default_factory=tuple)

    @property
    def flagged_candidates(self) -> tuple[NearDuplicateCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.requires_review)

    @property
    def is_clean(self) -> bool:
        return not self.issues and not self.flagged_candidates


def _tokenize_words(text: str) -> tuple[str, ...]:
    return tuple(_WORD_PATTERN.findall(text.lower()))


def _char_ngrams(text: str, *, n: int = _CHAR_NGRAM_SIZE) -> frozenset[str]:
    collapsed = _WHITESPACE_PATTERN.sub(" ", text.lower()).strip()
    if len(collapsed) < n:
        return frozenset({collapsed}) if collapsed else frozenset()
    return frozenset(collapsed[i : i + n] for i in range(len(collapsed) - n + 1))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _minhash_signature(shingles: frozenset[str]) -> tuple[int, ...]:
    """A real MinHash signature over a character-shingle set: for each of
    `_MINHASH_FUNCTIONS` independently-salted hash functions, the minimum
    digest value across every shingle. Two sets whose true Jaccard
    similarity is high are, in expectation, likely to agree on many of
    these minima -- the standard basis for `_lsh_bucket_keys`'s own banding
    below. An empty shingle set (an empty/whitespace-only text) gets a
    fixed all-zero signature so it still buckets deterministically instead
    of raising."""
    if not shingles:
        return tuple(0 for _ in range(_MINHASH_FUNCTIONS))
    signature: list[int] = []
    for salt in range(_MINHASH_FUNCTIONS):
        salt_bytes = salt.to_bytes(4, "big")
        min_value = min(
            int.from_bytes(
                hashlib.blake2b(salt_bytes + shingle.encode("utf-8")).digest()[:8], "big"
            )
            for shingle in shingles
        )
        signature.append(min_value)
    return tuple(signature)


def _lsh_bucket_keys(signature: tuple[int, ...]) -> tuple[str, ...]:
    """Bands `signature` into `_LSH_BANDS` groups of `_LSH_ROWS_PER_BAND`
    rows each and returns one bucket key per band. Two records sharing any
    one bucket key become a candidate pair; two records sharing none never
    are -- this is the real "bounded indexed... candidates" mechanism, not
    a naive all-pairs scan."""
    keys: list[str] = []
    for band in range(_LSH_BANDS):
        start = band * _LSH_ROWS_PER_BAND
        band_values = signature[start : start + _LSH_ROWS_PER_BAND]
        band_bytes = b"".join(value.to_bytes(8, "big") for value in band_values)
        keys.append(f"{band}:{hashlib.blake2b(band_bytes).hexdigest()}")
    return tuple(keys)


def _hashing_embedding(
    tokens: Sequence[str], *, dimensions: int = _EMBEDDING_DIMENSIONS
) -> tuple[float, ...]:
    """The "local/private embedding model" resolution this module's own
    docstring discloses: a deterministic feature-hashing vector (the
    "hashing trick"), never a pretrained/downloaded model. Each token
    contributes a signed unit to one hashed bucket; the accumulated vector
    is L2-normalized so cosine similarity behaves like a bounded
    similarity score. An all-zero (empty-token) vector is returned as-is;
    `_cosine_similarity` treats two all-zero vectors as identical (1.0),
    matching `_jaccard`'s own empty/empty convention above."""
    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = sum(component * component for component in vector) ** 0.5
    if norm == 0.0:
        return tuple(vector)
    return tuple(component / norm for component in vector)


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if all(component == 0.0 for component in a) and all(component == 0.0 for component in b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    # Both vectors are already L2-normalized by `_hashing_embedding`;
    # clamp for float round-off rather than trust the dot product blindly.
    return max(-1.0, min(1.0, dot))


_TEMPLATE_DIGIT_PATTERN = re.compile(r"\d+")
_TEMPLATE_WORD_PATTERN = re.compile(r"[a-zA-Z]+")


def _output_template(text: str) -> str:
    """A coarse structural signature for Section 10 method 7's own "same
    output template" check: every run of letters becomes `@`, every run of
    digits becomes `#`, everything else (punctuation, whitespace shape) is
    kept verbatim. Two assistant replies with the same scripted shape but
    different filled-in words/numbers ("Your order #1234 has shipped." vs.
    "Your order #9 has shipped.") reduce to the same template; two replies
    with a genuinely different shape do not."""
    lettered = _TEMPLATE_WORD_PATTERN.sub("@", text)
    return _TEMPLATE_DIGIT_PATTERN.sub("#", lettered)


@dataclass(frozen=True)
class _ConversationFeatures:
    normalized_text: str
    tokens: tuple[str, ...]
    shingles: frozenset[str]
    user_goal: str
    tool_sequence: tuple[str, ...]
    output_template: str


def _text_parts(message: Mapping[str, object], *, field_name: str) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        raise NearDuplicateDetectionError(f"{field_name}: content must be a list")
    parts: list[str] = []
    for index, part in enumerate(content):
        if not isinstance(part, Mapping):
            raise NearDuplicateDetectionError(f"{field_name}.content[{index}]: must be an object")
        part_type = part.get("type")
        if part_type not in _ALLOWED_CONTENT_TYPES:
            raise NearDuplicateDetectionError(
                f"{field_name}.content[{index}]: unsupported content type {part_type!r}"
            )
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise NearDuplicateDetectionError(
                    f"{field_name}.content[{index}]: text content part must carry a string 'text'"
                )
            parts.append(text)
    return parts


def _extract_features(content: object) -> _ConversationFeatures:
    if not isinstance(content, Mapping) or set(content) != _REQUIRED_CONTENT_KEYS:
        raise NearDuplicateDetectionError(
            "content must be an object with exactly the canonical 'messages'/'metadata' keys"
        )
    messages = content.get("messages")
    if not isinstance(messages, list) or not messages:
        raise NearDuplicateDetectionError("content.messages must be a non-empty list")

    all_text_parts: list[str] = []
    user_goal_parts: list[str] = []
    tool_sequence: list[str] = []
    last_assistant_text_parts: list[str] = []

    for index, message in enumerate(messages):
        field_name = f"messages[{index}]"
        if not isinstance(message, Mapping):
            raise NearDuplicateDetectionError(f"{field_name}: message must be an object")
        role = message.get("role")
        if role not in _ALLOWED_ROLES:
            raise NearDuplicateDetectionError(f"{field_name}: unmapped role {role!r}")

        text_parts = _text_parts(message, field_name=field_name)
        all_text_parts.extend(text_parts)
        if role == "user":
            user_goal_parts.extend(text_parts)
        if role == "assistant" and text_parts:
            last_assistant_text_parts = text_parts

        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls is not None:
            if not isinstance(raw_tool_calls, list):
                raise NearDuplicateDetectionError(f"{field_name}: tool_calls must be a list")
            for call_index, call in enumerate(raw_tool_calls):
                if not isinstance(call, Mapping):
                    raise NearDuplicateDetectionError(
                        f"{field_name}.tool_calls[{call_index}]: must be an object"
                    )
                name = call.get("name")
                if not isinstance(name, str) or not name:
                    raise NearDuplicateDetectionError(
                        f"{field_name}.tool_calls[{call_index}]: name must be a non-empty string"
                    )
                tool_sequence.append(name)

    normalized_text = _WHITESPACE_PATTERN.sub(" ", " ".join(all_text_parts).lower()).strip()
    user_goal = _WHITESPACE_PATTERN.sub(" ", " ".join(user_goal_parts).lower()).strip()
    output_template = _output_template(" ".join(last_assistant_text_parts))

    return _ConversationFeatures(
        normalized_text=normalized_text,
        tokens=_tokenize_words(normalized_text),
        shingles=_char_ngrams(normalized_text),
        user_goal=user_goal,
        tool_sequence=tuple(tool_sequence),
        output_template=output_template,
    )


@dataclass(frozen=True)
class _ValidCandidate:
    batch_position: int
    record_index: int | None
    purpose: str
    features: _ConversationFeatures
    embedding: tuple[float, ...]
    signature: tuple[int, ...]


def detect_near_duplicates(
    records: Sequence[Mapping[str, object]],
    *,
    thresholds: NearDuplicateThresholds = DEFAULT_THRESHOLDS,
    max_candidates_per_record: int = 50,
) -> NearDuplicateReport:
    """Detects Section 10 methods 4-7 ("near duplicate") candidates in an
    already-normalized candidate batch. Every candidate must carry
    `content` (FTF-0506's own canonical body shape) and `purpose` (Section
    6.8F's own closed vocabulary). Never raises on a per-row violation --
    returns a `NearDuplicateReport` whose `issues` carry row evidence.
    Raises `NearDuplicateDetectionError` only when the batch itself is not
    a real sequence of records (not a sequence, or empty), or when
    `thresholds`/`max_candidates_per_record` themselves are invalid.

    Every returned candidate is a *flag for review*, never an automatic
    discard -- see this module's own docstring for the full
    negative_acceptance mapping. `max_candidates_per_record` bounds memory
    use on a large, mostly-dissimilar corpus without ever falling back to
    an all-pairs comparison."""
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise NearDuplicateDetectionError("records must be a sequence of candidate records")
    if not records:
        raise NearDuplicateDetectionError("records must be a non-empty sequence")
    if not isinstance(max_candidates_per_record, int) or max_candidates_per_record < 1:
        raise NearDuplicateDetectionError("max_candidates_per_record must be a positive integer")

    issues: list[NearDuplicateIssue] = []
    valid: list[_ValidCandidate] = []

    for batch_position, candidate in enumerate(records):
        if not isinstance(candidate, Mapping):
            issues.append(
                NearDuplicateIssue(
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
                NearDuplicateIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_purpose",
                    message=f"purpose {purpose!r} is not a recognized value",
                )
            )
            continue

        try:
            features = _extract_features(candidate.get("content"))
        except NearDuplicateDetectionError as exc:
            issues.append(
                NearDuplicateIssue(
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
                features=features,
                embedding=_hashing_embedding(features.tokens),
                signature=_minhash_signature(features.shingles),
            )
        )

    buckets: dict[str, list[int]] = {}
    for position, item in enumerate(valid):
        for bucket_key in _lsh_bucket_keys(item.signature):
            buckets.setdefault(bucket_key, []).append(position)

    candidate_pairs: set[tuple[int, int]] = set()
    candidate_counts: dict[int, int] = {}
    for bucket_key in sorted(buckets):
        members = buckets[bucket_key]
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = (members[i], members[j])
                if pair in candidate_pairs:
                    continue
                if (
                    candidate_counts.get(pair[0], 0) >= max_candidates_per_record
                    or candidate_counts.get(pair[1], 0) >= max_candidates_per_record
                ):
                    continue
                candidate_pairs.add(pair)
                candidate_counts[pair[0]] = candidate_counts.get(pair[0], 0) + 1
                candidate_counts[pair[1]] = candidate_counts.get(pair[1], 0) + 1

    result_candidates: list[NearDuplicateCandidate] = []
    for position_a, position_b in sorted(candidate_pairs):
        item_a = valid[position_a]
        item_b = valid[position_b]

        character_ngram_similarity = _jaccard(item_a.features.shingles, item_b.features.shingles)
        token_jaccard_similarity = _jaccard(
            frozenset(item_a.features.tokens), frozenset(item_b.features.tokens)
        )
        embedding_cosine_similarity = _cosine_similarity(item_a.embedding, item_b.embedding)
        structural_match = (
            item_a.features.tool_sequence == item_b.features.tool_sequence
            and item_a.features.user_goal == item_b.features.user_goal
            and item_a.features.output_template == item_b.features.output_template
            and bool(item_a.features.tool_sequence or item_a.features.user_goal)
        )

        flagged_methods: set[str] = set()
        if character_ngram_similarity >= thresholds.character_ngram_similarity:
            flagged_methods.add("character_ngram")
        if token_jaccard_similarity >= thresholds.token_jaccard_similarity:
            flagged_methods.add("token_jaccard")
        if embedding_cosine_similarity >= thresholds.embedding_cosine_similarity:
            flagged_methods.add("embedding_cosine")
        if structural_match:
            flagged_methods.add("agent_trace_structural")

        if not flagged_methods:
            continue

        purposes = frozenset({item_a.purpose, item_b.purpose})
        result_candidates.append(
            NearDuplicateCandidate(
                batch_position_a=item_a.batch_position,
                batch_position_b=item_b.batch_position,
                record_index_a=item_a.record_index,
                record_index_b=item_b.record_index,
                character_ngram_similarity=character_ngram_similarity,
                token_jaccard_similarity=token_jaccard_similarity,
                embedding_cosine_similarity=embedding_cosine_similarity,
                structural_match=structural_match,
                flagged_methods=frozenset(flagged_methods),
                purposes=purposes,
                is_cross_purpose=len(purposes) > 1,
            )
        )

    return NearDuplicateReport(candidates=tuple(result_candidates), issues=tuple(issues))
