"""Critical behavior requirement consistency enforcement (FTF-0407,
Section 6.9/7A.6/22/65-66/77/81).

Section 7A.6's own mandate fields ("critical behaviors", "forbidden
responsibilities") and Section 6.8D's `AgentManifest.mandate_json`/
`forbidden_actions_json` do not exist as real, queryable storage anywhere
in this repository yet -- `AgentManifest` is FTF-04A19's own, later,
separately dependency-gated schema, and `docs/TASK_INDEX.md` records that
task as depending on *this* one (FTF-0407), not the other way around.
This task's own `database_changes` charter ("No independent schema
change. Consume frozen contract") rules out inventing a new mandate table
to fill that gap early.

The one real, already-frozen structural analog that exists today is
`ToolDefinition.is_critical`/`side_effect_class`/`requires_confirmation`
(FTF-0404, already CERTIFIED) plus FTF-0406's own already-live
`enforce_side_effect_classification`, which derives a real risk floor
from a tool's own name/description/schema evidence and forces
`requires_confirmation=True` on any tool resolved at or above
`irreversible_write`. Until this task, nothing checked `is_critical` --
Section 6.9's own literal "is this a critical responsibility" flag --
against those two fields at all: a purely read-only `list_orders` tool
could be created with `is_critical=True` (nothing for that claim to bind
a deterministic outcome test or runtime enforcement to), and an
`update_order_status`-shaped `reversible_write` tool could be created
`is_critical=True` with `requires_confirmation=False` (a "critical"
claim with no runtime guard behind it at all, since FTF-0406's own
confirmation floor only starts at `irreversible_write`).

This module closes exactly that gap, and only that gap -- the
"critical responsibility" half of acceptance clause 1
("Each critical responsibility/forbidden action links to deterministic
outcome tests and runtime enforcement"), narrowed to the one frozen field
triple that exists to link. The "forbidden action" half of that same
clause -- a full mandate-level forbidden-responsibilities list -- is
disclosed, not hidden, as FTF-04A19/FTF-04A22's own later scope once
`AgentManifest` is real; declaring it here would be exactly the
"placeholder" this task's own `database_changes` charter forbids.

`enforce_critical_behavior_requirement` makes this task's own
negative_acceptance ("Unmeasurable or conflicting critical mandate blocks
activation; no model-generated self-certification") real by construction:

- **Unmeasurable**: `is_critical=True` claimed on a tool resolved at
  `side_effect_class="read_only"` has no enforced side effect for a
  deterministic outcome test or runtime enforcement mechanism to bind
  to -- refused. (A `read_only` resolution is itself only ever reached
  when FTF-0406's own guard already found real read-only evidence and no
  risk signal at all -- see that module's own docstring -- so this can
  never be a false refusal of a genuinely dangerous tool.)
- **Conflicting**: `is_critical=True` claimed on a tool that has a real
  side effect (`side_effect_class` above `read_only`) but
  `requires_confirmation=False` -- the "critical" claim conflicts with
  its own complete lack of runtime enforcement. FTF-0406 already forces
  `requires_confirmation=True` unconditionally at or above
  `irreversible_write` regardless of `is_critical`, so this only adds
  real bite at `reversible_write` -- the one rank FTF-0406's own floor
  does not itself reach, and the one rank where a caller could otherwise
  mark a tool "critical" while leaving it completely unguarded.

Deliberately **not** enforced the other way: a tool resolved at or above
`irreversible_write` with `is_critical=False` is left alone. `is_critical`
is a distinct, user-authored "this is a critical business responsibility"
flag (Section 22 Screen D), not a mechanical restatement of the risk
floor -- FTF-0406's own already-CERTIFIED evidence
(`tests/tasks/test_ftf_0406.py::test_construction_accepts_a_correctly_confirmed_high_risk_tool`)
constructs exactly such a tool (`irreversible_write`, confirmed,
`is_critical=False`) and this task must not regress it (Law 14). Every
`irreversible_write`-or-above tool is already runtime-enforced via
`requires_confirmation` regardless of this flag; `is_critical` only adds
information for a human reviewing Screen D, it does not gate enforcement
on its own.

There is no caller-supplied "trust me" override anywhere in this module,
matching FTF-0406's own discipline exactly -- no description or mandate
text can move a tool across either boundary; only its own resolved
`side_effect_class`/`requires_confirmation` (themselves already
structurally validated, not merely claimed) can.

Wired into `app.tools.tool_definition_schema.ToolDefinition.__init__`,
the same live wiring point FTF-0402/FTF-0405/FTF-0406 already use, and
exposed as a real read-only `/requirements` view
(`app.tools.critical_behavior_requirement_routes`) -- this task's own
`api_contract` names `/requirements` as new surface FTF-0404's own
contract deliberately left undeclared for exactly this task.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.side_effect_classification import RISK_RANK

__all__ = [
    "CriticalBehaviorRequirementError",
    "CriticalBehaviorRequirementView",
    "derive_critical_behavior_requirement_view",
    "enforce_critical_behavior_requirement",
]


class CriticalBehaviorRequirementError(ValueError):
    """A claimed `is_critical` flag is unmeasurable (no real side effect
    to bind to) or conflicts with this tool's own lack of runtime
    confirmation enforcement."""


def enforce_critical_behavior_requirement(
    *, is_critical: bool, side_effect_class: str, requires_confirmation: bool
) -> None:
    """Refuse an `is_critical=True` claim that is unmeasurable or that
    conflicts with this tool's own resolved enforcement state. Called
    only after `side_effect_class`/`requires_confirmation` have themselves
    already been resolved/validated by `enforce_side_effect_classification`
    -- this function trusts those values structurally, it does not
    re-derive them. A no-op whenever `is_critical` is False -- this
    module never requires a tool to be marked critical, only refuses an
    unmeasurable or unenforced claim that it is."""
    rank = RISK_RANK.get(side_effect_class)
    if rank is None:
        raise CriticalBehaviorRequirementError(f"unknown side_effect_class: {side_effect_class!r}")
    if not is_critical:
        return

    if rank == RISK_RANK["read_only"]:
        raise CriticalBehaviorRequirementError(
            "is_critical=True on a side_effect_class='read_only' tool is unmeasurable -- a "
            "read-only operation has no enforced side effect for a deterministic outcome test "
            "or runtime enforcement mechanism to bind to; classify the tool's own real effect "
            "instead of marking a no-effect operation critical"
        )

    if not requires_confirmation:
        raise CriticalBehaviorRequirementError(
            f"is_critical=True on a side_effect_class={side_effect_class!r} tool with "
            "requires_confirmation=False is a conflicting mandate -- a critical responsibility "
            "claim with no runtime confirmation guard behind it is not real enforcement; set "
            "requires_confirmation=True or the claim cannot be accepted"
        )


@dataclass(frozen=True)
class CriticalBehaviorRequirementView:
    """The real, already-enforced requirement state for one current tool
    version -- never a separately stored claim, always derived fresh from
    the same frozen fields `enforce_critical_behavior_requirement` already
    validated at write time."""

    tool_definition_id: str
    name: str
    side_effect_class: str
    is_critical: bool
    requires_confirmation: bool
    measurable: bool
    runtime_enforced: bool


def derive_critical_behavior_requirement_view(
    *,
    tool_definition_id: str,
    name: str,
    side_effect_class: str,
    is_critical: bool,
    requires_confirmation: bool,
) -> CriticalBehaviorRequirementView:
    """Read-only projection for the `/requirements` view. Every row this
    is ever called on already passed `enforce_critical_behavior_requirement`
    at write time (the only way a `ToolDefinition` row can exist), so
    `measurable`/`runtime_enforced` here describe *why* that row is
    consistent, not a re-validation of it."""
    rank = RISK_RANK.get(side_effect_class, RISK_RANK["read_only"])
    return CriticalBehaviorRequirementView(
        tool_definition_id=tool_definition_id,
        name=name,
        side_effect_class=side_effect_class,
        is_critical=is_critical,
        requires_confirmation=requires_confirmation,
        measurable=rank > RISK_RANK["read_only"],
        runtime_enforced=requires_confirmation,
    )
