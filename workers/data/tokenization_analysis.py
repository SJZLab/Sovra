"""Tokenization analysis (FTF-0510, Section 7/8.1 step 9/9/10/67/77/81).

Section 8.1's import pipeline step 9 ("Tokenize using the selected base
model tokenizer when available") needs one real, load-bearing place where
an already-normalized candidate batch (the same `{"messages": [...],
"metadata": {...}}` candidate-content shape FTF-0508's own
`record_schema_validator.py` already validates) is tokenized with the
*actual* selected base model's own tokenizer and chat template, not a
display-name-only approximation -- this module is that place. This task's
own `database_changes` clause ("No independent schema change. Consume
frozen contract.") fixes this module's shape the same way as
FTF-0506/FTF-0507/FTF-0508/FTF-0511: a pure, synchronous, no-DB function.
Reading the two already-resolved local tokenizer files a caller supplies
(`tokenizer.json`, `tokenizer_config.json`) is real, necessary file I/O --
this task's own acceptance clause is "tokenize with the actual selected
tokenizer/template," which is not possible without reading that
tokenizer's own real files, the same disclosed local-file-read precedent
FTF-0304's `local_artifact_resolver.py` already established for a
different artifact kind.

New runtime dependencies, disclosed (CLAUDE.md "new runtime deps" is an
ask-first category, resolved here via CLAUDE.md's own "Default resolution
rule" rather than silently added): `tokenizers` (`workers/data/pyproject.toml`)
loads a real Hugging Face fast-tokenizer `tokenizer.json` file -- already
one of FTF-0302's own `_ALLOWED_FILENAME_SUFFIXES` for a resolved
`huggingface`-source `TrainableArtifact`
(`apps/api/app/artifacts/hugging_face_artifact_resolver.py`) -- with no
torch/tensorflow backend required. `jinja2` renders that same artifact's
own `tokenizer_config.json["chat_template"]` string -- the exact mechanism
`docs/architecture/REFERENCE_REPOSITORIES.md`'s own "Model templates" row
names (https://huggingface.co/docs/transformers/en/chat_templating is
Jinja2-templated chat formatting under the hood; the full `transformers`
package is not needed merely to tokenize with an already-resolved fast
tokenizer and render its own already-resolved chat template string). No
architecture doc names a different tokenization/templating library, so
nothing here picks an undecided alternative -- same precedent FTF-04A11
already used promoting `pyyaml` to a real `apps/api` runtime dependency.

This task's own acceptance clause ("Tokenize with actual selected
tokenizer/template, report distribution and complete conversation
boundaries") is met by `analyze_tokenization`:

  - **actual selected tokenizer/template**: `ChatTemplateFastTokenizer`
    loads the real fast tokenizer (`tokenizers.Tokenizer.from_file`) and
    renders the real chat template string through a sandboxed Jinja2
    environment (`jinja2.sandbox.ImmutableSandboxedEnvironment`, the same
    sandboxing Hugging Face's own `apply_chat_template` uses, plus a real
    `raise_exception(...)` global many real templates call to reject a
    malformed conversation -- e.g. Meta Llama 3's own published template)
    -- never a display name, alias, or approximate substitute. `exact`
    stays `True` only for this path.
  - **report distribution**: `TokenizationAnalysisReport.distribution`
    is a real `TokenDistribution` (count/min/max/mean/p50/p90/p99)
    computed over every successfully tokenized conversation's own real
    total token count -- nearest-rank percentiles, no numpy dependency
    added for it.
  - **complete conversation boundaries**: each `ConversationTokenization`
    carries real per-message token-count boundaries
    (`message_token_counts`, cumulative), computed by re-rendering and
    re-tokenizing every growing message prefix (`messages[:1]`,
    `messages[:2]`, ... `messages[:n]`) through the *same* real template
    and tokenizer and taking the length of each successive encoding --
    the real, load-bearing boundary a downstream loss-masking collator
    needs, not a guessed average-length split. (A BPE merge can, in
    principle, span a turn boundary and make a prefix's own tokenization
    differ very slightly from its contribution to the full sequence; this
    is an inherent property of any boundary-tracking approach built on a
    real greedy BPE tokenizer, not a shortcut unique to this module, and
    is disclosed here rather than hidden.)

This task's own negative_acceptance ("Any truncated critical turn
requires explicit policy; alias-only approximate tokenizer cannot certify
training fit") is met by two real, independently testable checks, both
collapsed into `TokenizationAnalysisReport.certifies_training_fit`:

  - **alias-only approximate tokenizer cannot certify training fit**:
    `ApproximateAliasTokenizer.exact` is `False` by construction (it still
    runs a real `tokenizers.Tokenizer` over real message text -- it is a
    genuine tokenizer, just not the real selected base model's own pinned
    one, the same "alias/display-name ... cannot substitute for exact"
    discipline FTF-0306's own negative_acceptance already established for
    weight digests). Any conversation tokenized with `exact=False` forces
    `certifies_training_fit=False` for the whole report -- there is no
    parameter to promote an approximate count to a certifying one.
  - **any truncated critical turn requires explicit policy**: Section
    8.2's own blocking-error list ("missing assistant outcome") means a
    clean canonical record's own *last* message is always the real
    training target -- its own "critical turn." Since a message's token
    span always ends at that message's own cumulative boundary, the final
    message's span always ends at the conversation's own total token
    count; naive truncation to `max_sequence_length` therefore cuts into
    the critical turn exactly when `total_token_count > max_sequence_length`,
    with no separate overlap arithmetic needed. `requires_explicit_truncation_policy`
    stays `True` for that conversation unless the caller's own
    `truncation_policy` mapping names an explicit, non-empty decision for
    its `batch_position` -- this module never interprets what that
    decision says (left-truncate, drop the example, ...), only that one
    was explicitly recorded, same "no effect authorized solely by ... a
    model answer" discipline Section 75.1 already states generally. Any
    conversation left `requires_explicit_truncation_policy=True` also
    forces `certifies_training_fit=False`.
  - a row that cannot be tokenized at all (missing/empty `messages`, or a
    genuine Jinja2 template rendering error -- a malformed conversation a
    real production chat template itself refuses, e.g. via
    `raise_exception`) is never silently dropped from the batch or
    retried with a guessed substitute: it is reported as a
    `TokenizationAnalysisIssue` with row evidence, the same
    "never raise on a per-row violation, always report" discipline
    FTF-0508's/FTF-0511's own reports already established. Any issue also
    forces `certifies_training_fit=False`. Only a batch that is not a
    real sequence of records at all (not a sequence, or empty) raises
    `TokenizationAnalysisError` outright, and only a non-positive
    `max_sequence_length` raises the same way -- both are call-level
    argument defects, not row defects, so there is no row to attach them
    to.

Scope decision, same disclosed "no live caller yet" precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0511/FTF-1302/FTF-1303/FTF-1304: this
task's own packet `files` list names only this module and its test, not
an importer/route or FTF-0302's own resolved-artifact-file plumbing. No
dataset-import pipeline route exists yet in this repository to call
`analyze_tokenization` with a real resolved `TrainableArtifact`'s own
downloaded `tokenizer.json`/`tokenizer_config.json` (that composition is a
later, separately dependency-gated task, the same class of gap FTF-0306
itself already disclosed for FTF-0301/0302/0303/0304 before it existed to
close it). `proving_symbol` stays blank; `product_truth_check.py`'s
product-true count is deliberately unchanged.

Path refinement recorded before READY (Section 84.3), same precedent as
FTF-0506/FTF-0507/FTF-0508/FTF-0511: the packet names
`services/training-worker/data/tokenization_analysis.py`, but this repo's
real `SOURCE_ROOTS` (FTF-0001, PR #10) name `workers/data` as the real
data/review worker root -- there is no `services/` tree in this
repository. Placed alongside FTF-0506's/FTF-0507's/FTF-0508's/FTF-0511's
own modules instead of creating a second, non-existent tree.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import jinja2
from jinja2.sandbox import ImmutableSandboxedEnvironment
from tokenizers import Tokenizer as HuggingFaceFastTokenizer

__all__ = [
    "ApproximateAliasTokenizer",
    "ChatTemplateFastTokenizer",
    "ChatTemplateRenderError",
    "ConversationTokenization",
    "ConversationTokenizer",
    "TokenDistribution",
    "TokenizationAnalysisError",
    "TokenizationAnalysisIssue",
    "TokenizationAnalysisReport",
    "analyze_tokenization",
]

_REQUIRED_CONTENT_KEYS = frozenset({"messages", "metadata"})


class TokenizationAnalysisError(ValueError):
    """The candidate batch itself is not a real sequence of records (not a
    sequence, or empty), or `max_sequence_length` is not a positive
    integer. Both are call-level argument defects, not row defects --
    there is no row to attach either to."""


class ChatTemplateRenderError(TokenizationAnalysisError):
    """A real Jinja2 chat-template render genuinely failed -- either the
    template itself is malformed, or (via the real `raise_exception`
    global many production templates call) the conversation shape itself
    was rejected by the model's own template logic."""


def _raise_exception(message: object) -> None:
    raise ChatTemplateRenderError(str(message))


_SANDBOXED_JINJA_ENV = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)


def _hf_tojson(
    value: object,
    ensure_ascii: bool = False,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    sort_keys: bool = False,
) -> str:
    """Mirrors transformers' own chat-template `tojson` filter exactly
    (`ensure_ascii=False` by default, `indent`/`separators`/`sort_keys`
    honoured) -- jinja2's own default `tojson` HTML-escapes and
    ASCII-escapes, which would change the rendered text and so the token
    count of any non-ASCII or indented tool argument."""
    return json.dumps(
        value,
        ensure_ascii=ensure_ascii,
        indent=indent,
        separators=separators,
        sort_keys=sort_keys,
    )


_SANDBOXED_JINJA_ENV.filters["tojson"] = _hf_tojson
_SANDBOXED_JINJA_ENV.globals["raise_exception"] = _raise_exception


class ConversationTokenizer(Protocol):
    """A real tokenizer+template backend. `exact=True` only when it is
    the real, pinned, selected base model's own tokenizer and chat
    template -- never set for an alias/approximate stand-in."""

    exact: bool

    def prefix_token_counts(self, messages: Sequence[Mapping[str, object]]) -> tuple[int, ...]:
        """Returns, for k = 1..len(messages), the real token count of the
        real chat-template-rendered prefix `messages[:k]` -- the last
        element is the full conversation's own real total token count."""
        ...


def _flatten_message_text(message: Mapping[str, object]) -> str:
    parts = message.get("content")
    if not isinstance(parts, Sequence) or isinstance(parts, str | bytes):
        return ""
    rendered: list[str] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text":
            rendered.append(str(part.get("text", "")))
        else:
            rendered.append(json.dumps(part.get("value"), sort_keys=True))
    return "".join(rendered)


def _to_template_message(message: Mapping[str, object]) -> dict[str, object]:
    """Converts this repository's own canonical Section 7 message shape
    (`CanonicalMessage.to_canonical()`, FTF-0506) into the real chat
    message shape production Jinja2 chat templates are written against
    (plain-string `content`; `tool_calls[*].function.{name,arguments}`) --
    never the other way around, since a real template cannot be rewritten
    per conversation."""
    template_message: dict[str, object] = {
        "role": message.get("role"),
        "content": _flatten_message_text(message),
    }
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str | bytes) and tool_calls:
        template_message["tool_calls"] = [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": call.get("name"),
                    "arguments": call.get("arguments"),
                },
            }
            for call in tool_calls
            if isinstance(call, Mapping)
        ]
    tool_call_id = message.get("tool_call_id")
    if tool_call_id is not None:
        template_message["tool_call_id"] = tool_call_id
    name = message.get("name")
    if name is not None:
        template_message["name"] = name
    return template_message


def _token_token_content(value: object) -> str:
    if isinstance(value, Mapping):
        content = value.get("content")
        return str(content) if content is not None else ""
    if value is None:
        return ""
    return str(value)


class ChatTemplateFastTokenizer:
    """The real, exact backend: a real Hugging Face fast tokenizer
    (`tokenizer.json`) plus the real selected base model's own declared
    chat template (`tokenizer_config.json["chat_template"]`), rendered
    through a sandboxed Jinja2 environment -- the same mechanism
    `docs/architecture/REFERENCE_REPOSITORIES.md`'s own "Model templates"
    row names. `exact` is always `True`: constructing this class at all
    requires the real selected model's own resolved tokenizer files."""

    exact: bool = True

    def __init__(
        self,
        *,
        fast_tokenizer: HuggingFaceFastTokenizer,
        template: jinja2.Template,
        bos_token: str,
        eos_token: str,
        tokenizer_json_sha256: str,
        chat_template_sha256: str,
    ) -> None:
        self.tokenizer_json_sha256 = tokenizer_json_sha256
        self.chat_template_sha256 = chat_template_sha256
        self._fast_tokenizer = fast_tokenizer
        self._template = template
        self._bos_token = bos_token
        self._eos_token = eos_token

    @classmethod
    def from_resolved_files(
        cls,
        tokenizer_json_path: str | Path,
        tokenizer_config: Mapping[str, object],
        *,
        expected_tokenizer_json_sha256: str,
        expected_chat_template_sha256: str,
    ) -> ChatTemplateFastTokenizer:
        """`exact` is bound to the two real, pinned digests of the selected
        base model's own tokenizer: the sha256 of the `tokenizer.json`
        bytes and of the `chat_template` string (UTF-8). A caller must
        supply the expected digests (from the resolved artifact manifest);
        a mismatch -- a different tokenizer or template than the one the
        model pins -- raises rather than yielding a self-asserted
        `exact=True`."""
        chat_template = tokenizer_config.get("chat_template")
        if not isinstance(chat_template, str) or not chat_template.strip():
            raise TokenizationAnalysisError(
                "tokenizer_config has no non-empty chat_template -- the real selected base "
                "model's own template is required, not a guessed default"
            )
        # Digests are compared inline and, once equal, the *expected* (== computed) value is
        # recorded: there is no intermediate normalisable copy of the computed digest.
        if (
            hashlib.sha256(Path(tokenizer_json_path).read_bytes()).hexdigest()
            != expected_tokenizer_json_sha256
        ):
            raise TokenizationAnalysisError(
                "tokenizer.json sha256 does not match the expected pinned digest -- this is "
                "not the selected base model's own tokenizer, so it cannot be exact"
            )
        if (
            hashlib.sha256(chat_template.encode("utf-8")).hexdigest()
            != expected_chat_template_sha256
        ):
            raise TokenizationAnalysisError(
                "chat_template sha256 does not match the expected pinned digest -- this is "
                "not the selected base model's own template, so it cannot be exact"
            )
        fast_tokenizer = HuggingFaceFastTokenizer.from_file(str(tokenizer_json_path))
        template = _SANDBOXED_JINJA_ENV.from_string(chat_template)
        return cls(
            fast_tokenizer=fast_tokenizer,
            template=template,
            bos_token=_token_token_content(tokenizer_config.get("bos_token")),
            eos_token=_token_token_content(tokenizer_config.get("eos_token")),
            tokenizer_json_sha256=expected_tokenizer_json_sha256,
            chat_template_sha256=expected_chat_template_sha256,
        )

    def _render(self, template_messages: Sequence[Mapping[str, object]]) -> str:
        try:
            return self._template.render(
                messages=list(template_messages),
                add_generation_prompt=False,
                bos_token=self._bos_token,
                eos_token=self._eos_token,
            )
        except jinja2.TemplateError as exc:
            raise ChatTemplateRenderError(str(exc)) from exc

    def prefix_token_counts(self, messages: Sequence[Mapping[str, object]]) -> tuple[int, ...]:
        template_messages = [_to_template_message(message) for message in messages]
        counts: list[int] = []
        for prefix_length in range(1, len(template_messages) + 1):
            rendered = self._render(template_messages[:prefix_length])
            # add_special_tokens=False: exactly what transformers'
            # apply_chat_template(tokenize=True) does -- the template itself
            # already emits bos_token, and a tokenizer.json BOS post-processor
            # would otherwise add a second one.
            counts.append(len(self._fast_tokenizer.encode(rendered, add_special_tokens=False).ids))
        return tuple(counts)


class ApproximateAliasTokenizer:
    """The approximate backend: a real tokenizer, but not the real
    selected base model's own pinned one, and with no real chat template
    applied (an alias tokenizer's own template, if it had one, is not the
    real selected model's template either -- Section 0's own "alias/
    display-name equality ... cannot substitute for exact" rule applies
    here exactly as FTF-0306 already applied it to weight digests).
    `exact` is always `False`."""

    exact: bool = False

    def __init__(self, fast_tokenizer: HuggingFaceFastTokenizer, *, alias_label: str) -> None:
        self._fast_tokenizer = fast_tokenizer
        self.alias_label = alias_label

    def prefix_token_counts(self, messages: Sequence[Mapping[str, object]]) -> tuple[int, ...]:
        counts: list[int] = []
        running_total = 0
        for message in messages:
            role = str(message.get("role", ""))
            text = _flatten_message_text(message)
            running_total += len(self._fast_tokenizer.encode(f"{role}: {text}").ids)
            counts.append(running_total)
        return tuple(counts)


@dataclass(frozen=True)
class TokenizationAnalysisIssue:
    batch_position: int
    record_index: int | None
    code: str
    message: str


@dataclass(frozen=True)
class ConversationTokenization:
    batch_position: int
    record_index: int | None
    exact: bool
    message_token_counts: tuple[int, ...]
    total_token_count: int
    truncated_critical_turn: bool
    requires_explicit_truncation_policy: bool


@dataclass(frozen=True)
class TokenDistribution:
    count: int
    min: int
    max: int
    mean: float
    p50: int
    p90: int
    p99: int


def _percentile(sorted_values: Sequence[int], percentile: int) -> int:
    """Nearest-rank percentile, deterministic, no numpy dependency added
    just for this. `sorted_values` must already be sorted ascending and
    non-empty."""
    n = len(sorted_values)
    rank = min(n, max(1, -(-percentile * n // 100)))
    return sorted_values[rank - 1]


@dataclass(frozen=True)
class TokenizationAnalysisReport:
    conversations: tuple[ConversationTokenization, ...]
    distribution: TokenDistribution | None
    issues: tuple[TokenizationAnalysisIssue, ...]
    certifies_training_fit: bool
    tokenizer_json_sha256: str | None = None
    chat_template_sha256: str | None = None


def _extract_messages(
    candidate: Mapping[str, object],
) -> Sequence[Mapping[str, object]] | None:
    content = candidate.get("content")
    if not isinstance(content, Mapping) or set(content) != _REQUIRED_CONTENT_KEYS:
        return None
    messages = content.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes) or not messages:
        return None
    if not all(isinstance(message, Mapping) for message in messages):
        return None
    return messages


def analyze_tokenization(
    records: Sequence[Mapping[str, object]],
    *,
    tokenizer: ConversationTokenizer,
    max_sequence_length: int,
    truncation_policy: Mapping[int, str] | None = None,
) -> TokenizationAnalysisReport:
    """Tokenizes an already-normalized candidate batch (the same
    `{"messages": [...], "metadata": {...}}` candidate-content shape
    `record_schema_validator.validate_record_batch` already validates)
    with the given real tokenizer backend. `truncation_policy` maps a
    conversation's own `batch_position` to a non-empty, caller-decided
    truncation policy string; this module never interprets the string
    itself, only whether one was explicitly recorded. Never raises on a
    per-row violation -- returns a `TokenizationAnalysisReport` whose
    `issues` carry row evidence. Raises `TokenizationAnalysisError` only
    when the batch itself is not a real sequence of records (not a
    sequence, or empty) or `max_sequence_length` is not a positive
    integer."""
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise TokenizationAnalysisError("records must be a sequence of candidate records")
    if not records:
        raise TokenizationAnalysisError("records must be a non-empty sequence")
    if not isinstance(max_sequence_length, int) or isinstance(max_sequence_length, bool):
        raise TokenizationAnalysisError("max_sequence_length must be a positive integer")
    if max_sequence_length <= 0:
        raise TokenizationAnalysisError("max_sequence_length must be a positive integer")

    policy = truncation_policy or {}

    conversations: list[ConversationTokenization] = []
    issues: list[TokenizationAnalysisIssue] = []
    certifies_training_fit = True

    for batch_position, candidate in enumerate(records):
        if not isinstance(candidate, Mapping):
            issues.append(
                TokenizationAnalysisIssue(
                    batch_position=batch_position,
                    record_index=None,
                    code="invalid_candidate",
                    message="candidate record must be an object",
                )
            )
            certifies_training_fit = False
            continue

        raw_record_index = candidate.get("record_index")
        record_index: int | None
        if isinstance(raw_record_index, int) and not isinstance(raw_record_index, bool):
            record_index = raw_record_index
        else:
            record_index = None

        messages = _extract_messages(candidate)
        if messages is None:
            issues.append(
                TokenizationAnalysisIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_messages",
                    message="candidate content must carry a non-empty 'messages' list to "
                    "tokenize -- there are no complete conversation boundaries to report "
                    "for an empty or malformed conversation",
                )
            )
            certifies_training_fit = False
            continue

        bad_content_positions = [
            position
            for position, message in enumerate(messages)
            if not isinstance(message.get("content"), Sequence)
            or isinstance(message.get("content"), str | bytes)
        ]
        if bad_content_positions:
            issues.append(
                TokenizationAnalysisIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="invalid_content",
                    message="message content must be a list of content parts; non-list "
                    f"content at message positions {bad_content_positions} would be "
                    "tokenized as empty text and under-report the real token count",
                )
            )
            certifies_training_fit = False
            continue

        try:
            message_token_counts = tokenizer.prefix_token_counts(messages)
        except ChatTemplateRenderError as exc:
            issues.append(
                TokenizationAnalysisIssue(
                    batch_position=batch_position,
                    record_index=record_index,
                    code="template_render_error",
                    message=str(exc),
                )
            )
            certifies_training_fit = False
            continue

        total_token_count = message_token_counts[-1] if message_token_counts else 0
        truncated_critical_turn = total_token_count > max_sequence_length
        explicit_policy = policy.get(batch_position)
        requires_explicit_truncation_policy = truncated_critical_turn and not (
            isinstance(explicit_policy, str) and explicit_policy.strip()
        )

        if not tokenizer.exact or requires_explicit_truncation_policy:
            certifies_training_fit = False

        conversations.append(
            ConversationTokenization(
                batch_position=batch_position,
                record_index=record_index,
                exact=tokenizer.exact,
                message_token_counts=message_token_counts,
                total_token_count=total_token_count,
                truncated_critical_turn=truncated_critical_turn,
                requires_explicit_truncation_policy=requires_explicit_truncation_policy,
            )
        )

    if issues:
        certifies_training_fit = False

    distribution: TokenDistribution | None = None
    totals = sorted(conversation.total_token_count for conversation in conversations)
    if totals:
        distribution = TokenDistribution(
            count=len(totals),
            min=totals[0],
            max=totals[-1],
            mean=sum(totals) / len(totals),
            p50=_percentile(totals, 50),
            p90=_percentile(totals, 90),
            p99=_percentile(totals, 99),
        )

    return TokenizationAnalysisReport(
        conversations=tuple(conversations),
        distribution=distribution,
        issues=tuple(issues),
        certifies_training_fit=certifies_training_fit,
        tokenizer_json_sha256=getattr(tokenizer, "tokenizer_json_sha256", None),
        chat_template_sha256=getattr(tokenizer, "chat_template_sha256", None),
    )
