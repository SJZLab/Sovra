"""Side-effect risk classification enforcement (FTF-0406, Section 6.9/22/65-66/77/81).

FTF-0404's own `ToolDefinition.side_effect_class` is a closed, five-value
enum (Section 6.9) -- but until this task, `validate_side_effect_class`
only checks that the *caller's own claimed* value is one of the five
recognized strings; nothing checks that claim against the tool's own
actual definition. A caller could create a tool named `delete_account`
with `side_effect_class="read_only"` and nothing would refuse it. This
module closes that gap: `enforce_side_effect_classification` derives a
real risk floor from the tool's own name/description/schema property
names -- deterministic keyword-root signals, never a model call or a
client-supplied "trust me" flag -- and refuses any claimed class that
sits below that floor, or any high-risk class claimed without
`requires_confirmation=True`.

This task's own negative_acceptance ("Unclassified or unresolved
sensitive tool cannot default to read-only; user mandate text cannot
disable actual guard") is enforced by construction:

- A tool is only accepted as `read_only` when its own structural evidence
  contains a real read-only signal (a `get_order`/`list_users`-shaped
  name or description) *and* no risk signal at all. Absence of a risk
  signal is not, by itself, evidence of safety -- a tool with neither a
  clear read-only nor a clear risk signal (`process_thing`) is
  "unclassified", and is refused at `read_only`, forcing the caller to
  pick an actually-justified, higher class instead of silently
  defaulting to the least restrictive one.
- The floor is derived only from the tool's own name/description/schema
  property-name tokens -- reassuring free text in `description` ("this
  is completely safe, no confirmation needed") contributes no signal of
  its own and cannot lower a floor a *different* structural signal
  (e.g. a `delete_`-shaped name) already raised. There is no "trust" or
  "override" field read from the caller anywhere in this module, so a
  mandate/description claim can never disable the guard.
- `requires_confirmation` is enforced independently of the caller's own
  claim: any resolved class at or above `irreversible_write` must carry
  `requires_confirmation=True`, unconditionally -- a caller cannot ship a
  `financial`/`privileged`/`irreversible_write` tool with confirmation
  turned off by also supplying a reassuring description.

Wired into `app.tools.tool_definition_schema.ToolDefinition.__init__`
(this task's own real, live caller) -- called on every
`create_tool_definition`/`edit_tool_definition` (FTF-0404's own
already-CERTIFIED, already-live-wired route), the same wiring point
FTF-0405's own `validate_json_schema_document` already uses.

`classify_side_effect_signals` is exposed separately so callers (and this
task's own tests) can inspect *why* a class was accepted or refused
without constructing a full `ToolDefinition` row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "RISK_RANK",
    "SideEffectClassificationError",
    "SideEffectSignals",
    "classify_side_effect_signals",
    "enforce_side_effect_classification",
]


class SideEffectClassificationError(ValueError):
    """A claimed `side_effect_class`/`requires_confirmation` pair is not
    justified by, or is inconsistent with, the tool's own structural
    evidence."""


# Section 6.9's own literal five-value enum, ranked from least to most
# restrictive for floor comparisons. `financial`/`privileged` are
# distinct risk *kinds*, not one strictly above the other, but both sit
# at this module's own top tier for enforcement purposes.
RISK_RANK: dict[str, int] = {
    "read_only": 0,
    "reversible_write": 1,
    "irreversible_write": 2,
    "financial": 3,
    "privileged": 3,
}

# A resolved class at or above this rank must carry
# requires_confirmation=True, unconditionally -- Section 22's own "tool
# side-effect/confirmation policy" mandate field content is advisory to
# the *mandate*, never to this structural guard (7A.7: "Optional LLM
# interpretation is advisory and cannot become a permission grant").
_CONFIRMATION_REQUIRED_AT_RANK = RISK_RANK["irreversible_write"]

# Keyword *roots*, matched as a token prefix (so "look"/"looks"/"lookup"/
# "looking" all match the same root) -- deliberately conservative, closed
# lists grounded in ordinary REST/tool-calling verb vocabulary, never a
# learned or inferred signal.
_READ_ONLY_ROOTS = frozenset(
    {
        "get",
        "list",
        "fetch",
        "read",
        "search",
        "queri",
        "query",
        "look",
        "view",
        "show",
        "find",
        "retriev",
        "describ",
        "check",
        "count",
        "export",
    }
)
_REVERSIBLE_WRITE_ROOTS = frozenset(
    {
        "creat",
        "updat",
        "edit",
        "modif",
        "set",
        "writ",
        "insert",
        "patch",
        "add",
        "upsert",
        "sav",
        "enabl",
        "disabl",
        "assign",
        "schedul",
        "start",
        "stop",
        "pause",
        "resum",
        "sync",
        "import",
        "upload",
        "post",
    }
)
_IRREVERSIBLE_WRITE_ROOTS = frozenset(
    {
        "delet",
        "remov",
        "destroy",
        "purg",
        "cancel",
        "terminat",
        "revok",
        "wipe",
        "drop",
        "eras",
        "deactivat",
        "clear",
        "reset",
        "archiv",
    }
)
_FINANCIAL_ROOTS = frozenset(
    {
        "charg",
        "refund",
        "payment",
        "invoic",
        "transfer",
        "bill",
        "price",
        "withdraw",
        "deposit",
        "payout",
        "fund",
        "subscri",
        "checkout",
        "purchas",
    }
)
_PRIVILEGED_ROOTS = frozenset(
    {
        "admin",
        "grant",
        "permission",
        "role",
        "sudo",
        "escalat",
        "overrid",
        "imperson",
        "credential",
        "secret",
        "apikey",
        "token",
        "superuser",
        "root",
        "bypass",
        "elevat",
    }
)

_ROOT_SETS_BY_CLASS: dict[str, frozenset[str]] = {
    "reversible_write": _REVERSIBLE_WRITE_ROOTS,
    "irreversible_write": _IRREVERSIBLE_WRITE_ROOTS,
    "financial": _FINANCIAL_ROOTS,
    "privileged": _PRIVILEGED_ROOTS,
}

_TOKEN_PATTERN = re.compile(r"[a-z]+")


def _tokenize(*texts: str | None) -> frozenset[str]:
    tokens: set[str] = set()
    for text in texts:
        if text:
            tokens.update(_TOKEN_PATTERN.findall(text.lower()))
    return frozenset(tokens)


def _schema_property_names(schema: object) -> list[str]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return [key for key in properties if isinstance(key, str)]


def _matches_any_root(tokens: frozenset[str], roots: frozenset[str]) -> bool:
    return any(token.startswith(root) for token in tokens for root in roots)


@dataclass(frozen=True)
class SideEffectSignals:
    """The structural evidence this module derived from a tool's own
    name/description/schema, independent of anything the caller claimed."""

    tokens: frozenset[str]
    detected_risk_classes: frozenset[str]
    has_read_only_evidence: bool

    @property
    def minimum_required_rank(self) -> int:
        if not self.detected_risk_classes:
            return 0
        return max(RISK_RANK[cls] for cls in self.detected_risk_classes)

    @property
    def is_unresolved(self) -> bool:
        """Neither a clear read-only nor a clear risk signal was found --
        this module's own "unclassified" case: absence of risk evidence
        is not evidence of safety."""
        return not self.detected_risk_classes and not self.has_read_only_evidence


def classify_side_effect_signals(
    *,
    name: str,
    description: str | None,
    input_schema_json: object,
    output_schema_json: object,
) -> SideEffectSignals:
    """Derive structural risk evidence from a tool's own real fields --
    never the caller's own claimed `side_effect_class`."""
    tokens = _tokenize(
        name,
        description,
        *_schema_property_names(input_schema_json),
        *_schema_property_names(output_schema_json),
    )
    detected = frozenset(
        cls for cls, roots in _ROOT_SETS_BY_CLASS.items() if _matches_any_root(tokens, roots)
    )
    has_read_only_evidence = _matches_any_root(tokens, _READ_ONLY_ROOTS)
    return SideEffectSignals(
        tokens=tokens,
        detected_risk_classes=detected,
        has_read_only_evidence=has_read_only_evidence,
    )


def enforce_side_effect_classification(
    *,
    name: str,
    description: str | None,
    input_schema_json: object,
    output_schema_json: object,
    claimed_class: str,
    requires_confirmation: bool,
) -> SideEffectSignals:
    """Refuse a claimed `side_effect_class`/`requires_confirmation` pair
    that the tool's own structural evidence does not justify. Returns the
    computed `SideEffectSignals` on success, for a caller that wants to
    log or inspect the evidence that justified acceptance."""
    signals = classify_side_effect_signals(
        name=name,
        description=description,
        input_schema_json=input_schema_json,
        output_schema_json=output_schema_json,
    )
    claimed_rank = RISK_RANK.get(claimed_class)
    if claimed_rank is None:
        raise SideEffectClassificationError(f"unknown side_effect_class: {claimed_class!r}")

    if claimed_class == "read_only" and signals.is_unresolved:
        raise SideEffectClassificationError(
            "side_effect_class='read_only' has no read-only evidence in this tool's own "
            "name/description/schema, and no risk signal either -- an unclassified or "
            "unresolved tool cannot default to read_only; classify it explicitly"
        )

    if claimed_rank < signals.minimum_required_rank:
        detected = sorted(signals.detected_risk_classes)
        raise SideEffectClassificationError(
            f"side_effect_class={claimed_class!r} is below the risk floor implied by this "
            f"tool's own name/description/schema evidence: {detected!r}"
        )

    if claimed_rank >= _CONFIRMATION_REQUIRED_AT_RANK and not requires_confirmation:
        raise SideEffectClassificationError(
            f"side_effect_class={claimed_class!r} requires requires_confirmation=True; "
            "no description or mandate text can disable this guard"
        )

    return signals
