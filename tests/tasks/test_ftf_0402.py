#!/usr/bin/env python3
"""FTF-0402 -- Project security profile (no-DB unit/fault tests).

Runnable without Postgres or network egress (the real live-database
evidence that `create_project`/`update_project` actually enforce this at
the API surface -- not merely a module that exists unused -- is
`tests/integration/test_ftf_0402_project_security_profile.py`, run in
CI's dedicated `project-security-profile-integration` job, the same
disclosed-container-registry-egress-gap precedent every prior
integration-tested task in this repository already carries). This suite
proves, without any database:

1. `SECURITY_PROFILE_LEVELS`/`validate_security_profile_name`/
   `security_profile_rank`: a closed, ordered set -- any name outside it
   is refused, never silently treated as the least (or most) restrictive.
2. `resolve_effective_security_profile`: always the more restrictive of
   an organization's own default and a project's own selection.
3. `enforce_security_profile_selection`: this task's own
   negative_acceptance ("User cannot relax inherited organization policy
   by changing a project field or using direct API") proven at the
   pure-function level -- a requested profile strictly less restrictive
   than the organization default is refused; an equal or stricter one is
   accepted unchanged.
4. `require_profile_at_least`: the general-purpose minimum-profile guard
   future consuming operations (datasets/source/worker/models/judges/
   telemetry) will call.
5. Source-inspection proof that `apps/api/app/projects/project_crud.py`'s
   `create_project`/`update_project` genuinely call
   `enforce_security_profile_selection` (real wiring into the one live
   surface that exists today -- FTF-0401's own project CRUD -- not an
   unused module).

Run directly: python3 tests/tasks/test_ftf_0402.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from app.services.project_security_profile import (  # noqa: E402
    SECURITY_PROFILE_LEVELS,
    InsufficientSecurityProfileError,
    SecurityProfileRelaxationError,
    UnknownSecurityProfileError,
    enforce_security_profile_selection,
    require_profile_at_least,
    resolve_effective_security_profile,
    security_profile_rank,
    validate_security_profile_name,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _rejects(fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except (
        UnknownSecurityProfileError,
        SecurityProfileRelaxationError,
        InsufficientSecurityProfileError,
    ):
        return True


def test_levels_are_the_expected_closed_ordered_set() -> None:
    check(
        "FTF-0402",
        "SECURITY_PROFILE_LEVELS is exactly (standard, zero_egress, air_gapped), "
        "in least- to most-restrictive order",
        SECURITY_PROFILE_LEVELS == ("standard", "zero_egress", "air_gapped"),
    )


def test_validate_security_profile_name_accepts_only_the_closed_set() -> None:
    for name in SECURITY_PROFILE_LEVELS:
        check(
            "FTF-0402",
            f"validate_security_profile_name accepts the real name {name!r}",
            validate_security_profile_name(name) == name,
        )
    check(
        "FTF-0402",
        "an unrecognized profile name is refused, not silently accepted",
        _rejects(validate_security_profile_name, "super_secure"),
    )
    check(
        "FTF-0402",
        "a non-string value is refused",
        _rejects(validate_security_profile_name, None),
    )
    check(
        "FTF-0402",
        "an empty string is refused",
        _rejects(validate_security_profile_name, ""),
    )


def test_rank_is_strictly_increasing_in_declared_order() -> None:
    ranks = [security_profile_rank(name) for name in SECURITY_PROFILE_LEVELS]
    check(
        "FTF-0402",
        "each successive profile's rank is strictly greater than the last "
        "(a real total order, not an arbitrary/tied mapping)",
        ranks == sorted(ranks) and len(set(ranks)) == len(ranks),
    )
    check(
        "FTF-0402",
        "security_profile_rank refuses an unrecognized name rather than guessing a rank for it",
        _rejects(security_profile_rank, "unknown_profile"),
    )


def test_resolve_effective_security_profile_is_always_the_more_restrictive() -> None:
    check(
        "FTF-0402",
        "org standard + project standard resolves to standard",
        resolve_effective_security_profile("standard", "standard") == "standard",
    )
    check(
        "FTF-0402",
        "org standard + project air_gapped resolves to the project's own "
        "stricter choice (air_gapped)",
        resolve_effective_security_profile("standard", "air_gapped") == "air_gapped",
    )
    check(
        "FTF-0402",
        "org air_gapped + project standard resolves to the organization's "
        "own stricter floor (air_gapped), never the project's looser value",
        resolve_effective_security_profile("air_gapped", "standard") == "air_gapped",
    )
    check(
        "FTF-0402",
        "org zero_egress + project zero_egress resolves to zero_egress",
        resolve_effective_security_profile("zero_egress", "zero_egress") == "zero_egress",
    )


def test_enforce_security_profile_selection_blocks_relaxation() -> None:
    # negative_acceptance, made real at the pure-function level: a
    # requested profile less restrictive than the organization's own
    # inherited default is refused outright.
    check(
        "FTF-0402",
        "negative_acceptance: requesting 'standard' under an org default of "
        "'air_gapped' is refused (cannot relax inherited organization policy)",
        _rejects(enforce_security_profile_selection, "air_gapped", "standard"),
    )
    check(
        "FTF-0402",
        "negative_acceptance: requesting 'standard' under an org default of "
        "'zero_egress' is refused",
        _rejects(enforce_security_profile_selection, "zero_egress", "standard"),
    )
    check(
        "FTF-0402",
        "negative_acceptance: requesting 'zero_egress' under an org default "
        "of 'air_gapped' is refused",
        _rejects(enforce_security_profile_selection, "air_gapped", "zero_egress"),
    )
    check(
        "FTF-0402",
        "an equal request (org default == requested) is accepted and returned unchanged",
        enforce_security_profile_selection("zero_egress", "zero_egress") == "zero_egress",
    )
    check(
        "FTF-0402",
        "a stricter request than the org default is accepted -- a project "
        "MAY choose a stricter profile than its organization's own floor",
        enforce_security_profile_selection("standard", "air_gapped") == "air_gapped",
    )
    check(
        "FTF-0402",
        "an unrecognized requested profile is refused, not silently accepted",
        _rejects(enforce_security_profile_selection, "standard", "made_up_profile"),
    )
    check(
        "FTF-0402",
        "an unrecognized organization default is refused rather than treated "
        "as the least-restrictive floor",
        _rejects(enforce_security_profile_selection, "made_up_profile", "standard"),
    )


def _accepts(fn, *args: object, **kwargs: object) -> bool:
    fn(*args, **kwargs)
    return True


def test_require_profile_at_least() -> None:
    check(
        "FTF-0402",
        "an effective profile that meets the required minimum is accepted",
        _accepts(require_profile_at_least, "air_gapped", "zero_egress"),
    )
    check(
        "FTF-0402",
        "an effective profile exactly equal to the required minimum is accepted",
        _accepts(require_profile_at_least, "zero_egress", "zero_egress"),
    )
    check(
        "FTF-0402",
        "an effective profile below the required minimum is refused",
        _rejects(require_profile_at_least, "standard", "air_gapped"),
    )


def test_project_crud_genuinely_calls_the_enforcement_primitive() -> None:
    # Source-inspection, the same discipline
    # tests/tasks/test_ftf_0401.py already applies to prove
    # execute_tenant_scoped is genuinely used rather than a bare
    # session.get/session.execute: confirms create_project/update_project
    # actually call enforce_security_profile_selection, not merely import
    # it unused.
    crud_source = Path(API_ROOT / "app" / "projects" / "project_crud.py").read_text(
        encoding="utf-8"
    )
    check(
        "FTF-0402",
        "project_crud.py imports the real enforcement primitive from this task's own module",
        "from app.services.project_security_profile import enforce_security_profile_selection"
        in crud_source,
    )
    call_count = crud_source.count("enforce_security_profile_selection(")
    check(
        "FTF-0402",
        "enforce_security_profile_selection is actually called (not just "
        "imported) -- at least once for create_project and once for "
        "update_project",
        call_count >= 2,
    )
    check(
        "FTF-0402",
        "create_project resolves the calling organization's own real "
        "default_security_profile before constructing the Project row",
        "_organization_default_security_profile(\n        session, context\n    )" in crud_source
        or "_organization_default_security_profile(session, context)" in crud_source,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0402 Project security profile tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0402 Project security profile tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
