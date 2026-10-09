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
`capture_effective_generation_settings` takes an OpenAI-compatible
chat/responses body and keeps only what is safe and needed to detect a
changed setting: a top-level key named in `GENERATION_SETTINGS_KEYS` (an
allow-list) is kept verbatim; a key in `CONTENT_KEYS` (`messages`, `input`,
`tools`, `model`, ...) is never read; any other key is not kept verbatim
but replaced by a `ContentDigest` (the sha256 of the value's canonical
form), so a bridge that adds, drops or rewrites an unrecognised parameter
is still seen as a difference without the value being captured. `extra_body`
is handled the same way one level down (`EXTRA_BODY_SETTINGS_KEYS`).
Round 2's deny-list could not be completed (each review round found more
missed content keys) and it deleted content-named keys at any depth, which
also erased real JSON-schema properties named `model`, `input` or `user`;
the allow-list never looks at key names inside a kept value, so a changed
`response_format` schema is a visible change. `compare_generation_parity`
then compares the approved baseline, the settings observed just before the
bridge runs and the settings observed just after it, key by key.

Values are limited to None, bool, int, float, str, bytes, `ContentDigest`
and list/tuple/dict of those (dict keys are scalars, kept and compared by
exact type, so `{50256: -100.0}` is not `{"50256": -100.0}`). Any other type
(a set, a non-dict mapping, a generator, a lock, an object with its own
`__eq__`) raises `GenerationConfigParityError` instead of being copied or
compared by Python `==`.

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

Unknown or malformed input (a non-mapping, a non-string key, an
unsupported value type, a malformed `DeclaredGenerationChange`) raises
`GenerationConfigParityError`. It never raises bare `TypeError`/`KeyError`
and never resolves to a passing verdict.

Round 2 (L2 return, 2026-10-09T07:48Z): a key is "preserved" only when
*every* value this module actually holds for it -- `approved` and, when
present, `observed_before` -- agrees exactly with `observed_after`. A
disagreeing `observed_before` (the bridge already rewrote the setting on
its way in) is never silently waved through just because `observed_after`
happens to match `approved` again; it blocks, and the approved-vs-before
disagreement itself is surfaced on the result (`approved_before_
disagreement_keys`), never just discarded by picking one side. The
"Recorded reading" above still answers which single value is compared
against `observed_after` when only one of `approved`/`observed_before`
names a key at all (then there is nothing to disagree about).

A `declared_change` is also checked for staleness: a declared key that
never actually appears in `observed_after`, or whose `observed_after`
value does not exactly match what was declared, blocks
(`stale_declared_keys`) -- a declaration that does not match reality is
never silently accepted just because nothing else happened to flag that
key.

The result carries stable `reason_codes` (`generation_config_dropped`,
`generation_config_changed`, `bridge_default_inherited`) describing which
blocking categories are present, and a `declared_change` verdict carries
the excusing `compatibility_tuple_sha256` so a caller never has to
re-derive why a blocked-looking difference actually passed.

Round 3 (L2 return, 2026-10-09T12:10Z): capture switched from a content-key
deny-list to the allow-list plus digest described above; nested non-string
keys are kept; unsupported value types and any copy failure raise the typed
error; set/frozenset/non-dict mappings are refused rather than compared
loosely.

No live caller yet: the Inspect runtime bridge that calls this module is
FTF-R004-P3 (not yet dependency-ready). `workers/evaluation` is this
repository's real evaluation-worker root; no schema change, no new runtime
dependency, no network call.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

__all__ = [
    "CONTENT_KEYS",
    "EXTRA_BODY_SETTINGS_KEYS",
    "GENERATION_SETTINGS_KEYS",
    "REASON_BRIDGE_DEFAULT_INHERITED",
    "REASON_GENERATION_CONFIG_CHANGED",
    "REASON_GENERATION_CONFIG_DROPPED",
    "ContentDigest",
    "DeclaredGenerationChange",
    "GenerationConfigParityError",
    "GenerationParityResult",
    "capture_effective_generation_settings",
    "compare_generation_parity",
]

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# A deliberately generous bound, well past any real generation-settings
# payload's own nesting (the real values here are scalars, short lists and
# shallow dicts like `response_format`/`stop`). Exists only so a cyclic or
# maliciously over-deep structure raises this module's own typed error
# instead of a bare, untyped RecursionError.
_MAX_NESTING_DEPTH = 64

REASON_GENERATION_CONFIG_DROPPED = "generation_config_dropped"
REASON_GENERATION_CONFIG_CHANGED = "generation_config_changed"
REASON_BRIDGE_DEFAULT_INHERITED = "bridge_default_inherited"

# The top-level allow-list: only these keys are captured verbatim. Every
# other non-content key is captured as a ContentDigest, never verbatim.
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
    "text",
    "logit_bias",
    "logprobs",
    "top_logprobs",
    "n",
    "top_k",
    "min_p",
    "repetition_penalty",
    "service_tier",
    "truncation",
    "verbosity",
)

# `extra_body` is a passthrough container: its children are captured verbatim
# only when named here (the sampling and guided-decoding knobs a vLLM-style
# server accepts); anything else in it is a ContentDigest.
EXTRA_BODY_SETTINGS_KEYS = (
    *GENERATION_SETTINGS_KEYS,
    "guided_json",
    "guided_regex",
    "guided_choice",
    "guided_grammar",
    "guided_decoding_backend",
)

# Keys that carry prompt/tool content or request identity/routing, never a
# generation setting. Never read by capture_effective_generation_settings,
# not even as a digest -- at the top level and directly inside `extra_body`
# (a passthrough container that can carry a `messages` of its own). They are
# not looked for inside a kept value: a JSON-schema property that happens to
# be named `model` is part of the setting, not content.
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
        "instructions",
        "prompt",
        "prediction",
        "conversation",
        "previous_response_id",
    }
)


class GenerationConfigParityError(ValueError):
    """Malformed input to this module (not a mapping, a non-string key, an
    invalid DeclaredGenerationChange). Never raised for a genuine parity
    finding -- a dropped/changed/inherited key is a normal, passed verdict
    inside GenerationParityResult, not an error."""


@dataclass(frozen=True)
class ContentDigest:
    """The sha256 of a value's canonical form, held instead of the value."""

    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or not _HEX64.fullmatch(self.sha256):
            raise GenerationConfigParityError("sha256 must be a lowercase hex64 string")


_SCALAR_TYPES = (type(None), bool, int, float, str, bytes, ContentDigest)


def _check_depth(depth: int) -> None:
    if depth > _MAX_NESTING_DEPTH:
        raise GenerationConfigParityError(
            "value nesting exceeds the maximum depth (cyclic or over-deep structure)"
        )


def _validate_settings_mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GenerationConfigParityError(f"{name} must be a mapping")
    for key in value:
        if type(key) is not str or key == "":
            raise GenerationConfigParityError(f"every {name} key must be a non-empty string")
    return value


def _copy_value(value: object, *, _depth: int = 0) -> object:
    """An independent copy of `value`, rebuilt only from the supported exact
    types. Nothing is deep-copied by reflection, so no `__deepcopy__`,
    `__reduce__` or `__eq__` of a caller object ever runs, and an
    unsupported type (a set, a non-dict mapping, a generator, a lock, a
    subclass) raises this module's own error naming only the type."""
    _check_depth(_depth)
    value_type = type(value)
    if value_type in _SCALAR_TYPES:
        return value
    if value_type is list:
        return [_copy_value(item, _depth=_depth + 1) for item in value]  # type: ignore[attr-defined]
    if value_type is tuple:
        return tuple(_copy_value(item, _depth=_depth + 1) for item in value)  # type: ignore[attr-defined]
    if value_type is dict:
        copied: dict[object, object] = {}
        for key, item in value.items():  # type: ignore[attr-defined]
            if type(key) not in _SCALAR_TYPES:
                raise GenerationConfigParityError(
                    f"unsupported mapping key type {type(key).__name__}"
                )
            copied[key] = _copy_value(item, _depth=_depth + 1)
        return copied
    raise GenerationConfigParityError(f"unsupported value type {value_type.__name__}")


def _copy_settings(value: object, *, name: str) -> dict[str, object]:
    validated = _validate_settings_mapping(value, name=name)
    return {key: _copy_value(item) for key, item in validated.items()}


def _tagged(value: object) -> str:
    """A canonical, type-tagged text form of an already-copied value (so `1`,
    `True`, `1.0` and `"1"` all digest differently, and dict order is
    irrelevant). Depth was already bounded by `_copy_value`."""
    value_type = type(value)
    if value_type is dict:
        entries = sorted(
            f"{_tagged(key)}:{_tagged(item)}"
            for key, item in value.items()  # type: ignore[attr-defined]
        )
        return "d{" + ",".join(entries) + "}"
    if value_type is list:
        return "l[" + ",".join(_tagged(item) for item in value) + "]"  # type: ignore[attr-defined]
    if value_type is tuple:
        return "t(" + ",".join(_tagged(item) for item in value) + ")"  # type: ignore[attr-defined]
    return f"{value_type.__name__}:{value!r}"


def _digest(value: object) -> ContentDigest:
    copied = _copy_value(value)
    return ContentDigest(hashlib.sha256(_tagged(copied).encode("utf-8")).hexdigest())


def _capture_extra_body(extra_body: Mapping[object, object]) -> dict[str, object]:
    captured: dict[str, object] = {}
    for key, value in extra_body.items():
        if type(key) is not str:
            raise GenerationConfigParityError("every extra_body key must be a string")
        if key in CONTENT_KEYS:
            continue
        if key in EXTRA_BODY_SETTINGS_KEYS:
            captured[key] = _copy_value(value)
        else:
            captured[key] = _digest(value)
    return captured


def capture_effective_generation_settings(
    request_body: Mapping[str, object],
) -> Mapping[str, object]:
    """Reduces an OpenAI-compatible chat/responses request body to its
    generation settings. A top-level key in GENERATION_SETTINGS_KEYS is kept
    verbatim (an independent copy); a key in CONTENT_KEYS is never read; any
    other key is kept only as a ContentDigest. `extra_body` is reduced the
    same way against EXTRA_BODY_SETTINGS_KEYS. This is not a copy of the
    request body: prompt, tool and identity content never survives."""
    validated = _validate_settings_mapping(request_body, name="request_body")
    settings: dict[str, object] = {}
    for key, value in validated.items():
        if key in CONTENT_KEYS:
            continue
        if key == "extra_body" and isinstance(value, Mapping):
            settings[key] = _capture_extra_body(value)
        elif key in GENERATION_SETTINGS_KEYS:
            settings[key] = _copy_value(value)
        else:
            settings[key] = _digest(value)
    return settings


def _exact_equal(left: object, right: object) -> bool:
    """`0.7 != "0.7"`, `True != 1`, `{1: x} != {True: x}`: exact type, not
    merely Python `==`. Recurses into dict/list/tuple so a changed
    `response_format` shape or `stop` list is caught, not waved through by
    `==` on the outer container alone. Both sides have already been through
    `_copy_value`, so only the supported exact types reach here and the
    nesting depth is already bounded."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        right_dict = cast(dict[object, object], right)
        left_items = {(type(key), key): item for key, item in left.items()}
        right_items = {(type(key), key): item for key, item in right_dict.items()}
        return left_items.keys() == right_items.keys() and all(
            _exact_equal(left_items[key], right_items[key]) for key in left_items
        )
    if isinstance(left, list | tuple):
        right_sequence = cast(list[object] | tuple[object, ...], right)
        return len(left) == len(right_sequence) and all(
            _exact_equal(item_left, item_right)
            for item_left, item_right in zip(left, right_sequence, strict=True)
        )
    return bool(left == right)


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
        # Freeze and copy: a frozen dataclass only blocks reassigning
        # `self.changed_values`, not mutating the mapping object itself. A
        # MappingProxyType over a fresh, typed copy means neither the
        # caller's original mapping nor anything handed back by this
        # dataclass can change `changed_values` after construction, and an
        # unsupported value type is this module's own error, never a bare
        # TypeError from a reflective deepcopy.
        frozen = MappingProxyType(_copy_settings(validated, name="changed_values"))
        object.__setattr__(self, "changed_values", frozen)


_VERDICTS = ("preserved", "declared_change", "blocked", "insufficient_evidence")


@dataclass(frozen=True)
class GenerationParityResult:
    """Key names only, never values -- see module docstring. `blocking_keys`
    is the exact union of `dropped_keys`, `changed_keys`,
    `inherited_default_keys` and `stale_declared_keys` that is NOT excused
    by `declared_change_keys`. `approved_before_disagreement_keys` is
    purely informational (it never excuses or forces a block by itself --
    `blocking_keys` already reflects every key this disagreement affects
    through the before-vs-after check). `reason_codes` names which
    blocking categories are present. `compatibility_tuple_sha256` is set
    only when `verdict == "declared_change"`."""

    verdict: str
    dropped_keys: tuple[str, ...]
    changed_keys: tuple[str, ...]
    inherited_default_keys: tuple[str, ...]
    declared_change_keys: tuple[str, ...]
    blocking_keys: tuple[str, ...]
    approved_before_disagreement_keys: tuple[str, ...] = ()
    stale_declared_keys: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    compatibility_tuple_sha256: str | None = None

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
    approved_validated = _copy_settings(approved, name="approved")
    if declared_change is not None and not isinstance(declared_change, DeclaredGenerationChange):
        raise GenerationConfigParityError(
            "declared_change must be a DeclaredGenerationChange or None"
        )

    if observed_before is None or observed_after is None:
        if observed_before is not None:
            _copy_settings(observed_before, name="observed_before")
        if observed_after is not None:
            _copy_settings(observed_after, name="observed_after")
        return GenerationParityResult(
            verdict="insufficient_evidence",
            dropped_keys=(),
            changed_keys=(),
            inherited_default_keys=(),
            declared_change_keys=(),
            blocking_keys=(),
        )

    before_validated = _copy_settings(observed_before, name="observed_before")
    after_validated = _copy_settings(observed_after, name="observed_after")
    declared_values = declared_change.changed_values if declared_change is not None else {}

    expected_keys = set(approved_validated) | set(before_validated)
    dropped: list[str] = []
    changed: list[str] = []
    declared_ok: list[str] = []
    approved_before_disagreement: list[str] = []
    for key in sorted(expected_keys):
        approved_present = key in approved_validated
        before_present = key in before_validated
        if (
            approved_present
            and before_present
            and not _exact_equal(approved_validated[key], before_validated[key])
        ):
            approved_before_disagreement.append(key)

        if key not in after_validated:
            dropped.append(key)
            continue
        after_value = after_validated[key]

        # Preserved only if every value this module actually holds for this
        # key -- approved, and before when present -- agrees exactly with
        # after. A disagreeing before is a real bridge rewrite and must not
        # be waved through just because after happens to match approved.
        matches_approved = (not approved_present) or _exact_equal(
            approved_validated[key], after_value
        )
        matches_before = (not before_present) or _exact_equal(before_validated[key], after_value)
        if matches_approved and matches_before:
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

    # A declared change that does not match reality -- the key never shows
    # up in `after` at all, or shows up with a different value than
    # declared -- is a stale declaration. It blocks even when nothing else
    # in this comparison happened to touch that key.
    stale_declared: list[str] = []
    stale_missing = False
    stale_mismatch = False
    for key in sorted(declared_values):
        if key not in after_validated:
            stale_declared.append(key)
            stale_missing = True
        elif not _exact_equal(declared_values[key], after_validated[key]):
            stale_declared.append(key)
            stale_mismatch = True

    blocking = tuple(sorted({*dropped, *changed, *inherited, *stale_declared}))
    is_declared_change = not blocking and bool(declared_ok)
    if blocking:
        verdict = "blocked"
    elif is_declared_change:
        verdict = "declared_change"
    else:
        verdict = "preserved"

    reason_codes = tuple(
        code
        for present, code in (
            (bool(dropped) or stale_missing, REASON_GENERATION_CONFIG_DROPPED),
            (bool(changed) or stale_mismatch, REASON_GENERATION_CONFIG_CHANGED),
            (bool(inherited), REASON_BRIDGE_DEFAULT_INHERITED),
        )
        if present
    )
    compatibility_tuple_sha256 = (
        declared_change.compatibility_tuple_sha256
        if is_declared_change and declared_change is not None
        else None
    )

    return GenerationParityResult(
        verdict=verdict,
        dropped_keys=tuple(dropped),
        changed_keys=tuple(changed),
        inherited_default_keys=tuple(inherited),
        declared_change_keys=tuple(sorted(declared_ok)),
        blocking_keys=blocking,
        approved_before_disagreement_keys=tuple(approved_before_disagreement),
        stale_declared_keys=tuple(stale_declared),
        reason_codes=reason_codes,
        compatibility_tuple_sha256=compatibility_tuple_sha256,
    )
