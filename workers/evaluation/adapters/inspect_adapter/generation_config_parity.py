"""FTF-R004-P1 -- Inspect bridge generation-config parity comparator.

Section 78.2 ("Explicit Inspect bridge parity") and Section 66.4-66.5: the
Inspect bridge sits between the Factory's approved candidate configuration
and the actual request the target runtime receives. A bridge library can
silently drop a sampling parameter, rename it, or inherit its own default --
none of that may pass as "preserved" without being noticed.

Acceptance ("Compare effective target generation settings before and after
the Inspect bridge; preserve the approved temperature/top_p/token/reasoning
settings or record an explicit candidate configuration change in the locked
compatibility tuple. Do not silently inherit bridge defaults."):
`capture_effective_generation_settings` reads only the generation-settings
keys of an OpenAI-compatible chat/responses body -- it never reads
`messages`, `tools`, `input`, `model` or any other content/identity-bearing
key (`CONTENT_KEYS`), so the comparator can never surface prompt or tool
content even if a caller hands it a full request body. `compare_generation_
parity` then compares the approved baseline, the settings observed just
before the bridge runs and the settings observed just after it, key by key.

Negative acceptance ("Reject silent generation-config dropping"): a key
present in `approved`/`observed_before` that is missing, or has a different
value, in `observed_after` blocks (`dropped_keys`/`changed_keys`) unless an
explicit `DeclaredGenerationChange` names that exact after-value under a
locked `compatibility_tuple_sha256`. A key that appears only in
`observed_after` blocks as `inherited_default_keys` -- a bridge default is
never silently accepted as if the candidate had approved it.

Values are compared exactly (`_exact_equal`): `0.7` is never `"0.7"`, and
`True` is never `1`, so a bridge that re-serializes a number as a string or
a bool as an int is caught, not waved through by Python's own `==`
(`True == 1`). A `None` `observed_before`/`observed_after` -- the caller
never captured that side of the bridge -- gives `insufficient_evidence`,
never `preserved`: an untested boundary can never certify as parity.

`GenerationParityResult` holds only key names, never values: the comparator
cannot leak a captured value (by construction it never holds prompt/tool
content in the first place, but the result stays content-free regardless of
what a caller passes as "settings"). `DeclaredGenerationChange` is the one
place caller-declared values are kept, because they are the candidate's own
declared configuration change, not captured runtime content, and they are
pinned to a locked `compatibility_tuple_sha256` (hex64) that names the
tuple recording the change -- a declared change with no digest is refused
outright, never treated as an ambient default.

Unknown or malformed input (a non-mapping, a non-string key, a malformed
`DeclaredGenerationChange`) raises `GenerationConfigParityError`. It never
raises bare `TypeError`/`KeyError` and never resolves to a passing verdict.

Recorded reading (default-resolution rule): when `approved` and
`observed_before` disagree on a shared key's value, `approved` -- the
locked, authorized baseline -- is treated as the expected value, since it
is named first in the acceptance text's own "approved or before" ordering
and is the one value the candidate configuration actually locked.

No live caller yet: the Inspect runtime bridge that calls this module is
FTF-R004-P3 (not yet dependency-ready). `workers/evaluation` is this
repository's real evaluation-worker root; no schema change, no new runtime
dependency, no network call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "CONTENT_KEYS",
    "GENERATION_SETTINGS_KEYS",
    "DeclaredGenerationChange",
    "GenerationConfigParityError",
    "GenerationParityResult",
    "capture_effective_generation_settings",
    "compare_generation_parity",
]

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Named generation-settings keys this module documents by name. Any other
# top-level key not in CONTENT_KEYS is still captured verbatim (a forward-
# compatible, unknown generation setting) -- naming these is documentation,
# not a filter.
GENERATION_SETTINGS_KEYS = (
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "reasoning_effort",
    "reasoning",
    "seed",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "tool_choice",
    "parallel_tool_calls",
    "response_format",
)

# Keys that carry prompt/tool content or request identity/routing, never a
# generation setting. Never captured by capture_effective_generation_settings,
# whatever else is present alongside them.
CONTENT_KEYS = frozenset(
    {
        "messages",
        "input",
        "tools",
        "functions",
        "function_call",
        "model",
        "stream",
        "user",
        "metadata",
    }
)


class GenerationConfigParityError(ValueError):
    """Malformed input to this module (not a mapping, a non-string key, an
    invalid DeclaredGenerationChange). Never raised for a genuine parity
    finding -- a dropped/changed/inherited key is a normal, passed verdict
    inside GenerationParityResult, not an error."""


def _validate_settings_mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GenerationConfigParityError(f"{name} must be a mapping")
    for key in value:
        if not isinstance(key, str) or key == "":
            raise GenerationConfigParityError(f"every {name} key must be a non-empty string")
    return value


def capture_effective_generation_settings(
    request_body: Mapping[str, object],
) -> Mapping[str, object]:
    """Reads only the generation-settings keys of an OpenAI-compatible
    chat/responses request body. `messages`, `tools`, `input`, `model` and
    the rest of CONTENT_KEYS are never read, whatever they contain."""
    validated = _validate_settings_mapping(request_body, name="request_body")
    settings: dict[str, object] = {}
    for key, value in validated.items():
        if key in CONTENT_KEYS:
            continue
        settings[key] = value
    return dict(settings)


def _exact_equal(left: object, right: object) -> bool:
    """`0.7 != "0.7"` and `True != 1`: exact type, not merely Python `==`
    (which treats `True == 1`). Recurses into dict/list/tuple so a changed
    `response_format` shape or `stop` list is caught, not waved through by
    `==` on the outer container alone."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        right_dict = right if isinstance(right, dict) else {}
        return left.keys() == right_dict.keys() and all(
            _exact_equal(left[key], right_dict[key]) for key in left
        )
    if isinstance(left, list | tuple):
        right_sequence = right if isinstance(right, list | tuple) else ()
        return len(left) == len(right_sequence) and all(
            _exact_equal(item_left, item_right)
            for item_left, item_right in zip(left, right_sequence, strict=True)
        )
    return left == right


@dataclass(frozen=True)
class DeclaredGenerationChange:
    """An explicit, candidate-declared generation-config change, pinned to
    the locked compatibility tuple that records it. Without this, any
    observed difference blocks -- this is the only way to pass one."""

    compatibility_tuple_sha256: str
    changed_values: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.compatibility_tuple_sha256, str) or not _HEX64.fullmatch(
            self.compatibility_tuple_sha256
        ):
            raise GenerationConfigParityError(
                "compatibility_tuple_sha256 must be a lowercase hex64 string"
            )
        validated = _validate_settings_mapping(self.changed_values, name="changed_values")
        if not validated:
            raise GenerationConfigParityError("changed_values must be a non-empty mapping")


_VERDICTS = ("preserved", "declared_change", "blocked", "insufficient_evidence")


@dataclass(frozen=True)
class GenerationParityResult:
    """Key names only, never values -- see module docstring. `blocking_keys`
    is the exact union of `dropped_keys`, `changed_keys` and
    `inherited_default_keys` that is NOT excused by `declared_change_keys`."""

    verdict: str
    dropped_keys: tuple[str, ...]
    changed_keys: tuple[str, ...]
    inherited_default_keys: tuple[str, ...]
    declared_change_keys: tuple[str, ...]
    blocking_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.verdict not in _VERDICTS:
            raise GenerationConfigParityError(f"verdict must be one of {_VERDICTS}")


def compare_generation_parity(
    approved: Mapping[str, object],
    observed_before: Mapping[str, object] | None,
    observed_after: Mapping[str, object] | None,
    declared_change: DeclaredGenerationChange | None = None,
) -> GenerationParityResult:
    """Compares the approved baseline against what was observed immediately
    before and after the Inspect bridge. See the module docstring for the
    exact blocking/declared-change/insufficient-evidence rules."""
    approved_validated = _validate_settings_mapping(approved, name="approved")
    if declared_change is not None and not isinstance(declared_change, DeclaredGenerationChange):
        raise GenerationConfigParityError(
            "declared_change must be a DeclaredGenerationChange or None"
        )

    if observed_before is None or observed_after is None:
        if observed_before is not None:
            _validate_settings_mapping(observed_before, name="observed_before")
        if observed_after is not None:
            _validate_settings_mapping(observed_after, name="observed_after")
        return GenerationParityResult(
            verdict="insufficient_evidence",
            dropped_keys=(),
            changed_keys=(),
            inherited_default_keys=(),
            declared_change_keys=(),
            blocking_keys=(),
        )

    before_validated = _validate_settings_mapping(observed_before, name="observed_before")
    after_validated = _validate_settings_mapping(observed_after, name="observed_after")
    declared_values = declared_change.changed_values if declared_change is not None else {}

    expected_keys = set(approved_validated) | set(before_validated)
    dropped: list[str] = []
    changed: list[str] = []
    declared_ok: list[str] = []
    for key in sorted(expected_keys):
        expected_value = (
            approved_validated[key] if key in approved_validated else before_validated[key]
        )
        if key not in after_validated:
            dropped.append(key)
            continue
        after_value = after_validated[key]
        if _exact_equal(expected_value, after_value):
            continue
        if key in declared_values and _exact_equal(declared_values[key], after_value):
            declared_ok.append(key)
            continue
        changed.append(key)

    inherited: list[str] = []
    for key in sorted(set(after_validated) - expected_keys):
        after_value = after_validated[key]
        if key in declared_values and _exact_equal(declared_values[key], after_value):
            declared_ok.append(key)
            continue
        inherited.append(key)

    blocking = tuple(sorted({*dropped, *changed, *inherited}))
    if blocking:
        verdict = "blocked"
    elif declared_ok:
        verdict = "declared_change"
    else:
        verdict = "preserved"

    return GenerationParityResult(
        verdict=verdict,
        dropped_keys=tuple(dropped),
        changed_keys=tuple(changed),
        inherited_default_keys=tuple(inherited),
        declared_change_keys=tuple(sorted(declared_ok)),
        blocking_keys=blocking,
    )
