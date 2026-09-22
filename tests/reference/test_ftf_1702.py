#!/usr/bin/env python3
"""FTF-1702 -- Tool environment (no-DB unit/fault tests).

Runnable without Postgres, GPU, or network egress -- this task's own
`resource_gate` clause is moot here, matching the FTF-1701 precedent
exactly: "implement a real disposable seeded backend" names no container,
HTTP endpoint or GPU prerequisite of its own. This suite proves, for real:

1. `ToolEnvironment` seeds real records (via the real
   `ReferenceBusinessServer.seed_record`) from a declared `ToolEnvironmentSeed`,
   and `record_id_for` resolves them by fixture triple.
2. `dispatch` is a real, undiminished caller of FTF-1701's own
   `dispatch_tool_call` -- a full five-tool happy path (search, get, create
   a note, request an approval, have it decided out-of-band, execute it)
   runs for real through this environment, including FTF-1701's own
   tenant/subject authorization and idempotency guarantees (no regression).
3. `dispatch` refuses a tenant or subject this environment's own seed spec
   never declared, before FTF-1701's own dispatcher is ever reached.
4. `reset()` is a genuine disposal, not an in-place clear: state mutated
   before a reset (a created note, an executed approval, a record's status
   change) is verifiably gone afterward, seeded records get freshly
   generated IDs, and `reset_count` accounts for every call.
5. Two independent `ToolEnvironment` instances never share state, and a
   custom `ToolEnvironmentSeed` is honored exactly as declared (no silent
   fallback to `DEFAULT_SEED`).

This task's own negative_acceptance ("Canned success tools and unguarded
financial writes cannot satisfy target-product test environment") is
covered by (2)+(3): every result comes from FTF-1701's own real handler
and real schema validation, and an unseeded identity is refused rather than
silently permitted or silently no-op'd.

Run directly: python3 tests/reference/test_ftf_1702.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(API_ROOT))

from evals.reference.reference_agent_specification import (  # noqa: E402
    ReferenceAuthorizationError,
)
from evals.reference.tool_environment import (  # noqa: E402
    DEFAULT_SEED,
    ToolEnvironment,
    ToolEnvironmentError,
    ToolEnvironmentSeed,
    build_tool_environment,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


def test_seeds_real_records_resolvable_by_fixture_triple() -> None:
    env = build_tool_environment()
    record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    check("FTF-1702", "a seeded record resolves to a real, non-empty record ID", bool(record_id))

    result = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id},
    )
    check(
        "FTF-1702",
        "the seeded record is genuinely readable through the real dispatcher",
        result["title"] == "Quarterly report" and result["status"] == "open",
    )

    check(
        "FTF-1702",
        "record_id_for raises ToolEnvironmentError for a triple never seeded",
        _raises(
            ToolEnvironmentError,
            env.record_id_for,
            tenant_id="tenant-a",
            owner_subject_id="subject-a1",
            title="Never seeded",
        ),
    )


def test_full_five_tool_happy_path_through_real_dispatcher() -> None:
    env = build_tool_environment()
    record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )

    search = env.dispatch(
        "search_records",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"query": "quarterly"},
    )
    check(
        "FTF-1702",
        "search_records finds the seeded record for its real owner",
        any(r["record_id"] == record_id for r in search["records"]),
    )

    note = env.dispatch(
        "create_note",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id, "text": "reviewed", "idempotency_key": "note-1"},
    )
    replay = env.dispatch(
        "create_note",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id, "text": "reviewed", "idempotency_key": "note-1"},
    )
    check(
        "FTF-1702",
        "create_note idempotency is preserved through this environment's dispatch",
        note["note_id"] == replay["note_id"],
    )

    approval = env.dispatch(
        "request_approval",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id, "action": "archive_record"},
    )
    check(
        "FTF-1702",
        "request_approval files a real pending approval through this environment",
        approval["status"] == "pending",
    )

    # decide_approval/revoke_approval are the deterministic independent
    # approver's own surface (FTF-1701), never a dispatchable tool name --
    # exercised directly against the real server, matching FTF-1701's own
    # precedent for "a deterministic independent server owns permission/
    # approval records."
    env.server.decide_approval(
        tenant_id="tenant-a",
        approver_subject_id="approver-1",
        approval_id=approval["approval_id"],
        decision="approved",
    )

    executed = env.dispatch(
        "execute_approved_action",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"approval_id": approval["approval_id"], "idempotency_key": "exec-1"},
    )
    check(
        "FTF-1702",
        "execute_approved_action genuinely executes an approved request through this environment",
        executed["status"] == "executed" and executed["record_id"] == record_id,
    )

    final = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id},
    )
    check(
        "FTF-1702",
        "the record's real state reflects the executed action (archived)",
        final["status"] == "archived",
    )


def test_dispatch_refuses_unseeded_tenant_before_reaching_real_dispatcher() -> None:
    env = build_tool_environment()
    check(
        "FTF-1702",
        "dispatch refuses a tenant this environment's own seed never declared",
        _raises(
            ToolEnvironmentError,
            env.dispatch,
            "search_records",
            tenant_id="tenant-never-seeded",
            subject_id="subject-a1",
            arguments={"query": "x"},
        ),
    )


def test_dispatch_refuses_unseeded_subject_for_a_real_seeded_tenant() -> None:
    env = build_tool_environment()
    check(
        "FTF-1702",
        "dispatch refuses a subject never declared for an otherwise real, seeded tenant",
        _raises(
            ToolEnvironmentError,
            env.dispatch,
            "search_records",
            tenant_id="tenant-a",
            subject_id="subject-never-seeded",
            arguments={"query": "x"},
        ),
    )


def test_dispatch_still_enforces_real_cross_tenant_authorization() -> None:
    # Both identities are genuinely seeded (this environment's own guard
    # does not apply), so this exercises FTF-1701's own, unmodified
    # cross-tenant authorization -- no regression introduced by wrapping it.
    env = build_tool_environment()
    record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    check(
        "FTF-1702",
        "cross-tenant record access is still refused for real through this environment",
        _raises(
            ReferenceAuthorizationError,
            env.dispatch,
            "get_record",
            tenant_id="tenant-b",
            subject_id="subject-b1",
            arguments={"record_id": record_id},
        ),
    )


def test_reset_is_a_genuine_disposal_not_an_in_place_clear() -> None:
    env = build_tool_environment()
    original_record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    env.dispatch(
        "create_note",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={
            "record_id": original_record_id,
            "text": "pre-reset note",
            "idempotency_key": "pre-reset",
        },
    )
    before_reset = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": original_record_id},
    )
    check(
        "FTF-1702",
        "the pre-reset mutation is genuinely present before reset",
        len(before_reset["notes"]) == 1,
    )

    server_before = env.server
    env.reset()

    check(
        "FTF-1702",
        "reset() discards the old server rather than mutating it",
        env.server is not server_before,
    )
    check(
        "FTF-1702",
        "reset() refuses to serve the pre-reset record ID (it never existed in the fresh server)",
        _raises(
            Exception,
            env.dispatch,
            "get_record",
            tenant_id="tenant-a",
            subject_id="subject-a1",
            arguments={"record_id": original_record_id},
        ),
    )

    new_record_id = env.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    check(
        "FTF-1702",
        "reset() assigns a freshly generated ID to the reseeded record, never reusing the old one",
        new_record_id != original_record_id,
    )
    after_reset = env.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": new_record_id},
    )
    check(
        "FTF-1702",
        "the reseeded record starts clean (no notes carried over from the disposed instance)",
        after_reset["notes"] == [] and after_reset["status"] == "open",
    )


def test_reset_count_accounts_for_every_reset() -> None:
    env = build_tool_environment()
    check("FTF-1702", "reset_count starts at zero", env.reset_count == 0)
    env.reset()
    env.reset()
    check("FTF-1702", "reset_count reflects exactly two real reset() calls", env.reset_count == 2)


def test_two_environments_never_share_state() -> None:
    env_a = build_tool_environment()
    env_b = build_tool_environment()
    record_id_a = env_a.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    env_a.dispatch(
        "create_note",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id_a, "text": "only in env_a", "idempotency_key": "k1"},
    )
    record_id_b = env_b.record_id_for(
        tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
    )
    result_b = env_b.dispatch(
        "get_record",
        tenant_id="tenant-a",
        subject_id="subject-a1",
        arguments={"record_id": record_id_b},
    )
    check(
        "FTF-1702",
        "a mutation in one ToolEnvironment is never visible in an independently built one",
        result_b["notes"] == [],
    )


def test_custom_seed_spec_is_honored_not_silently_replaced() -> None:
    custom_seed = ToolEnvironmentSeed(
        tenants=("tenant-z",),
        subjects_by_tenant={"tenant-z": ("subject-z1",)},
        records=(("tenant-z", "subject-z1", "Custom fixture record"),),
    )
    env = build_tool_environment(seed=custom_seed)

    check(
        "FTF-1702",
        "a custom seed's own tenant/subject is genuinely reachable",
        not _raises(
            Exception,
            env.dispatch,
            "search_records",
            tenant_id="tenant-z",
            subject_id="subject-z1",
            arguments={"query": "custom"},
        ),
    )
    check(
        "FTF-1702",
        "DEFAULT_SEED's own tenant is refused when a different seed spec was declared",
        _raises(
            ToolEnvironmentError,
            env.dispatch,
            "search_records",
            tenant_id="tenant-a",
            subject_id="subject-a1",
            arguments={"query": "x"},
        ),
    )
    check(
        "FTF-1702",
        "DEFAULT_SEED itself is untouched by constructing a custom-seeded environment",
        DEFAULT_SEED.tenants == ("tenant-a", "tenant-b"),
    )


def test_environment_type_is_reusable_without_module_globals() -> None:
    # Constructing ToolEnvironment directly (not through build_tool_environment)
    # must behave identically -- the factory function is convenience, not the
    # only valid construction path.
    env = ToolEnvironment()
    check(
        "FTF-1702",
        "ToolEnvironment() constructed directly seeds records exactly like the factory function",
        bool(
            env.record_id_for(
                tenant_id="tenant-a", owner_subject_id="subject-a1", title="Quarterly report"
            )
        ),
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1702 Tool environment tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-1702 Tool environment tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
