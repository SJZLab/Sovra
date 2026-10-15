#!/usr/bin/env python3
"""FTF-0906 -- Egress capture/verification (no-DB unit/fault tests).

Proves, without any database or network, the pure verifier
(`packages/security/egress_capture_verification.py`) that combines
enforcement, connection logs and plaintext/packet-capture inspection into
one report:

1. ACC-1 ("combine enforcement, connection logs and controlled
   destination/history/log plaintext inspection"): a fully-exercised
   allow+deny plan with matching evidence reports each flow `passed`.
2. NEG-1 ("encrypted packet capture alone cannot show no plaintext
   leakage; blocked and permitted flows both tested"): a flow whose only
   no-leak evidence is an encrypted capture is `incomplete`, never
   `passed`; both an allow flow and a deny flow are exercised.
3. Every other named rule (`flow_class_missing`, `connection_log_missing`,
   `unauthorized_transfer`, `vacuous_permitted_flow`, `canary_leaked`,
   `inspection_coverage_missing`, `workflow_history` forcing `partial`
   rather than `passed`) has its own positive and negative case.
4. `scan_store_for_canaries` is proven over every encoding (literal,
   base64, hex, url, fragmented) and fails closed (`CanaryLeakedError`)
   the moment a real canary is actually found -- the hard path Section 12
   itself requires, independent of `verify_egress`'s own report.
5. Fail-closed input handling (unknown names, wrong types) on every
   public dataclass/function.
6. Fault injection (Law 1): regex mutations of the real module, run in a
   fresh subprocess, each must change behaviour; the file is restored
   byte-identical.

Run directly: python3 tests/tasks/test_ftf_0906.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from packages.security.canary_secret_generator import (  # noqa: E402
    CanaryLeakedError,
    CanaryPlacementLedger,
    PlacementRecord,
    generate_canary,
)
from packages.security.egress_capture_verification import (  # noqa: E402
    ConnectionLogEntry,
    EgressEvidence,
    EgressPlan,
    EnforcementObservation,
    FlowClassMissingError,
    FlowVerdict,
    InvalidEgressEvidenceError,
    InvalidEgressPlanError,
    PacketCaptureObservation,
    PlaintextInspection,
    PlannedFlow,
    _aggregate_overall_status,
    scan_store_for_canaries,
    verify_egress,
)

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _raises(exc_type: type[BaseException], fn, *args: object, **kwargs: object) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return True
    except Exception:
        return False
    return False


_DIGEST = "a" * 64


def _flow(flow_id: str, expected: str, placements: tuple[PlacementRecord, ...] = ()) -> PlannedFlow:
    return PlannedFlow(
        flow_id=flow_id,
        surface="data",
        destination_label=f"dest-{flow_id}",
        destination_class="external" if expected == "deny" else "internal",
        expected=expected,
        canary_placements=placements,
    )


def _plan(
    flows: tuple[PlannedFlow, ...], required_stores: frozenset[str] | None = None
) -> EgressPlan:
    kwargs = {}
    if required_stores is not None:
        kwargs["required_stores"] = required_stores
    return EgressPlan(
        plan_id="plan-1", scope="process", policy_digest=_DIGEST, flows=flows, **kwargs
    )


# --- EgressPlan / PlannedFlow construction ----------------------------------


def test_plan_requires_both_an_allow_and_a_deny_flow() -> None:
    allow_only = (_flow("a1", "allow"),)
    check(
        "FTF-0906-NEG1",
        "a plan with only allow flows refuses construction",
        _raises(FlowClassMissingError, _plan, allow_only),
    )
    deny_only = (_flow("d1", "deny"),)
    check(
        "FTF-0906-NEG1",
        "a plan with only deny flows refuses construction",
        _raises(FlowClassMissingError, _plan, deny_only),
    )
    check(
        "FTF-0906-NEG1",
        "a plan with both classes constructs fine",
        bool(_plan((_flow("a1", "allow"), _flow("d1", "deny")))),
    )


def test_plan_fail_closed_inputs() -> None:
    good = (_flow("a1", "allow"), _flow("d1", "deny"))
    check(
        "FTF-0906-INPUT",
        "empty flows refused",
        _raises(InvalidEgressPlanError, _plan, ()),
    )
    check(
        "FTF-0906-INPUT",
        "duplicate flow_id refused",
        _raises(InvalidEgressPlanError, _plan, (_flow("a1", "allow"), _flow("a1", "deny"))),
    )
    check(
        "FTF-0906-INPUT",
        "bad scope refused",
        _raises(
            InvalidEgressPlanError,
            lambda: EgressPlan(plan_id="p", scope="cloud", policy_digest=_DIGEST, flows=good),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "non-hex policy_digest refused",
        _raises(
            InvalidEgressPlanError,
            lambda: EgressPlan(
                plan_id="p", scope="process", policy_digest="not-a-digest", flows=good
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "unknown required store refused",
        _raises(InvalidEgressPlanError, _plan, good, frozenset({"nope"})),
    )
    check(
        "FTF-0906-INPUT",
        "unknown surface refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlannedFlow(
                flow_id="x",
                surface="nope",
                destination_label="d",
                destination_class="external",
                expected="allow",
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "unknown expected decision refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlannedFlow(
                flow_id="x",
                surface="data",
                destination_label="d",
                destination_class="external",
                expected="maybe",
            ),
        ),
    )


# --- Evidence dataclass fail-closed checks ----------------------------------


def test_evidence_fail_closed_inputs() -> None:
    check(
        "FTF-0906-INPUT",
        "bad enforcement layer refused",
        _raises(
            InvalidEgressPlanError,
            lambda: EnforcementObservation(flow_id="a1", layer="nope", decision="allow", code="x"),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "bad log outcome refused",
        _raises(
            InvalidEgressPlanError,
            lambda: ConnectionLogEntry(flow_id="a1", side="client", outcome="nope"),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "bad store refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlaintextInspection(
                store="nope", store_id="s1", flow_id="a1", inspected=True, decrypted=False
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "canary_found at an uninspected store refused",
        _raises(
            InvalidEgressEvidenceError,
            lambda: PlaintextInspection(
                store="artifact",
                store_id="s1",
                flow_id="a1",
                inspected=False,
                decrypted=False,
                canary_found=True,
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "non-bool encrypted refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PacketCaptureObservation(
                flow_id="a1",
                encrypted="yes",  # type: ignore[arg-type]
                canary_found=False,
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "duplicate enforcement observation for one flow refused",
        _raises(
            InvalidEgressEvidenceError,
            lambda: verify_egress(
                _plan((_flow("a1", "allow"), _flow("d1", "deny"))),
                EgressEvidence(
                    enforcement=(
                        EnforcementObservation("a1", "process_guard", "allow", "ok"),
                        EnforcementObservation("a1", "process_guard", "deny", "dup"),
                    )
                ),
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "duplicate client-side connection log for one flow refused",
        _raises(
            InvalidEgressEvidenceError,
            lambda: verify_egress(
                _plan((_flow("a1", "allow"), _flow("d1", "deny"))),
                EgressEvidence(
                    connection_logs=(
                        ConnectionLogEntry("a1", "client", "connected"),
                        ConnectionLogEntry("a1", "client", "refused"),
                    )
                ),
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "duplicate packet capture for one flow refused",
        _raises(
            InvalidEgressEvidenceError,
            lambda: verify_egress(
                _plan((_flow("a1", "allow"), _flow("d1", "deny"))),
                EgressEvidence(
                    packet_captures=(
                        PacketCaptureObservation("a1", encrypted=True, canary_found=False),
                        PacketCaptureObservation("a1", encrypted=False, canary_found=False),
                    )
                ),
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a non-EgressPlan first argument to verify_egress is refused",
        _raises(InvalidEgressPlanError, verify_egress, "not-a-plan", EgressEvidence()),
    )
    check(
        "FTF-0906-INPUT",
        "a non-EgressEvidence second argument to verify_egress is refused",
        _raises(
            InvalidEgressEvidenceError,
            verify_egress,
            _plan((_flow("a1", "allow"), _flow("d1", "deny"))),
            "not-evidence",
        ),
    )
    check(
        "FTF-0906-INPUT",
        "an evidence field passed as a list instead of a tuple is refused",
        _raises(
            InvalidEgressEvidenceError,
            lambda: EgressEvidence(
                enforcement=[  # type: ignore[arg-type]
                    EnforcementObservation("a1", "process_guard", "allow", "ok")
                ]
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a wrongly-typed item inside an evidence tuple is refused",
        _raises(
            InvalidEgressEvidenceError,
            lambda: EgressEvidence(enforcement=("not-an-observation",)),  # type: ignore[arg-type]
        ),
    )


def test_none_and_length_boundary_inputs() -> None:
    check(
        "FTF-0906-INPUT",
        "a None flow_id is refused (never treated as present)",
        _raises(InvalidEgressPlanError, _flow, None, "allow"),
    )
    check(
        "FTF-0906-INPUT",
        "a None destination_label is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlannedFlow(
                flow_id="a1",
                surface="data",
                destination_label=None,  # type: ignore[arg-type]
                destination_class="internal",
                expected="allow",
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a None plan_id is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: EgressPlan(
                plan_id=None,  # type: ignore[arg-type]
                scope="process",
                policy_digest=_DIGEST,
                flows=(_flow("a1", "allow"), _flow("d1", "deny")),
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a None store_id is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlaintextInspection(
                store="artifact",
                store_id=None,  # type: ignore[arg-type]
                flow_id="a1",
                inspected=True,
                decrypted=False,
            ),
        ),
    )

    def _bounded_flow(flow_id: str) -> PlannedFlow:
        return PlannedFlow(
            flow_id=flow_id,
            surface="data",
            destination_label="d",
            destination_class="internal",
            expected="allow",
        )

    check(
        "FTF-0906-INPUT",
        "a flow_id at exactly the 200-character bound is accepted",
        bool(_bounded_flow("x" * 200)),
    )
    check(
        "FTF-0906-INPUT",
        "a flow_id one character past the bound is refused",
        _raises(InvalidEgressPlanError, _bounded_flow, "x" * 201),
    )


def test_member_rejects_case_and_whitespace_variants() -> None:
    # One case/whitespace-variant test per `_member`-validated field is enough
    # to kill a mutant inside `_member` itself (shared by every caller) --
    # unlike the many *downstream* `==` comparisons in `verify_egress` on
    # fields `_member` already forced to an exact lowercase member at
    # construction, where `.strip().lower()` is a true no-op for any value
    # that could ever reach them (disclosed in the submission as reviewed
    # equivalent mutants, not chased here).
    check(
        "FTF-0906-INPUT",
        "an uppercase scope is refused, not silently normalised",
        _raises(
            InvalidEgressPlanError,
            lambda: EgressPlan(
                plan_id="p",
                scope="PROCESS",
                policy_digest=_DIGEST,
                flows=(_flow("a1", "allow"), _flow("d1", "deny")),
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a whitespace-padded expected decision is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlannedFlow(
                flow_id="a1",
                surface="data",
                destination_label="d",
                destination_class="internal",
                expected=" allow",
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "an uppercase-hex policy_digest is still accepted (case-insensitive by design)",
        bool(
            EgressPlan(
                plan_id="p",
                scope="process",
                policy_digest="A" * 64,
                flows=(_flow("a1", "allow"), _flow("d1", "deny")),
            )
        ),
    )


def test_tuple_and_type_guards() -> None:
    check(
        "FTF-0906-INPUT",
        "canary_placements as a list instead of a tuple is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlannedFlow(
                flow_id="a1",
                surface="data",
                destination_label="d",
                destination_class="internal",
                expected="allow",
                canary_placements=[],  # type: ignore[arg-type]
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a non-PlacementRecord canary placement is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: PlannedFlow(
                flow_id="a1",
                surface="data",
                destination_label="d",
                destination_class="internal",
                expected="allow",
                canary_placements=("not-a-placement",),  # type: ignore[arg-type]
            ),
        ),
    )
    good_flows = (_flow("a1", "allow"), _flow("d1", "deny"))
    check(
        "FTF-0906-INPUT",
        "flows passed as a list instead of a tuple is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: EgressPlan(
                plan_id="p",
                scope="process",
                policy_digest=_DIGEST,
                flows=list(good_flows),  # type: ignore[arg-type]
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a non-PlannedFlow item in flows is refused",
        _raises(
            InvalidEgressPlanError,
            lambda: EgressPlan(
                plan_id="p",
                scope="process",
                policy_digest=_DIGEST,
                flows=(*good_flows, "bogus"),  # type: ignore[arg-type]
            ),
        ),
    )
    check(
        "FTF-0906-INPUT",
        "required_stores as a plain set instead of a frozenset is refused",
        _raises(InvalidEgressPlanError, _plan, good_flows, {"controlled_destination"}),
    )
    check(
        "FTF-0906-INPUT",
        "a non-CanaryPlacementLedger ledger argument is refused",
        _raises(
            InvalidEgressEvidenceError,
            scan_store_for_canaries,
            b"data",
            {},
            (),
            "not-a-ledger",
        ),
    )
    marker = generate_canary()
    ledger = CanaryPlacementLedger({("trace", "literal")})
    placement = PlacementRecord(marker.canary_id, "trace", "literal", "r1")
    ledger.record_placement(placement)
    check(
        "FTF-0906-INPUT",
        "a non-PlacementRecord item in placements is refused",
        _raises(
            InvalidEgressEvidenceError,
            scan_store_for_canaries,
            b"data",
            {marker.canary_id: marker},
            ("not-a-placement",),
            ledger,
        ),
    )
    check(
        "FTF-0906-INPUT",
        "a marker of the wrong type for a known canary id is refused",
        _raises(
            InvalidEgressEvidenceError,
            scan_store_for_canaries,
            b"data",
            {marker.canary_id: "not-a-marker"},
            (placement,),
            ledger,
        ),
    )


def test_max_flows_boundary() -> None:
    at_bound = tuple(_flow(f"f{i}", "allow" if i % 2 else "deny") for i in range(200))
    check("FTF-0906-INPUT", "exactly 200 flows is accepted", bool(_plan(at_bound)))
    over_bound = tuple(_flow(f"f{i}", "allow" if i % 2 else "deny") for i in range(201))
    check(
        "FTF-0906-INPUT", "201 flows is refused", _raises(InvalidEgressPlanError, _plan, over_bound)
    )


def test_distinct_case_flow_ids_are_never_merged_and_match_exactly() -> None:
    # `flow_id` is free-form text (only `_text`-bounded, never `_member`-
    # normalised), so two ids differing only by case are legitimately
    # distinct, and a real flow's own evidence must match it by exact id --
    # neither side of that match may silently fold case or whitespace.
    mixed_plan = EgressPlan(
        plan_id="p",
        scope="process",
        policy_digest=_DIGEST,
        flows=(
            PlannedFlow("flow-a", "data", "d", "external", "deny"),
            PlannedFlow("Flow-A", "data", "d", "internal", "allow"),
        ),
        required_stores=frozenset({"controlled_destination", "workflow_history"}),
    )
    check(
        "FTF-0906-INPUT",
        "two flow_ids differing only by case are distinct, not a duplicate",
        bool(mixed_plan),
    )
    # The already-lowercase id is listed FIRST in every evidence tuple and
    # the mixed-case id SECOND: a mutant that folds only the *duplicate-check
    # side* of an `in` test (never the stored key itself) is observable only
    # in this order -- the lowered form of the second, mixed-case id then
    # collides with the first, already-lowercase stored key, raising a
    # bogus duplicate error that the real module never raises.
    evidence = EgressEvidence(
        enforcement=(
            EnforcementObservation("flow-a", "process_guard", "deny", "blocked"),
            EnforcementObservation("Flow-A", "process_guard", "allow", "ok"),
        ),
        connection_logs=(
            ConnectionLogEntry("flow-a", "client", "refused"),
            ConnectionLogEntry("flow-a", "destination", "not_seen"),
            ConnectionLogEntry("Flow-A", "client", "connected"),
            ConnectionLogEntry("Flow-A", "destination", "connected"),
        ),
        plaintext_inspections=(
            PlaintextInspection("controlled_destination", "sink", "Flow-A", True, False),
            PlaintextInspection("workflow_history", "wf", None, True, False),
        ),
        packet_captures=(
            PacketCaptureObservation("flow-a", encrypted=False, canary_found=False),
            PacketCaptureObservation("Flow-A", encrypted=False, canary_found=False),
        ),
    )
    report = verify_egress(mixed_plan, evidence)
    check(
        "FTF-0906-RULE",
        "evidence matches its own flow by exact id, case-sensitively, end to end",
        all(v.status == "passed" for v in report.flow_verdicts) and report.status == "passed",
    )


def test_downstream_comparisons_are_exact_not_normalized() -> None:
    """Every field tampered below is `_member`-validated to an exact
    lowercase member at construction (see
    test_member_rejects_case_and_whitespace_variants) -- normal callers can
    never hand `verify_egress`/`scan_store_for_canaries` a non-canonical
    value. These checks go one step further: a frozen dataclass's own
    invariant is still only as strong as every one of its own callers, so
    this proves the downstream `==`/`!=`/`in` comparisons themselves stay
    exact, defense-in-depth, rather than silently re-normalizing a value a
    future bug might let through uncanonicalized. `object.__setattr__` is
    the only way to get a non-canonical value past a frozen dataclass's own
    `__post_init__` -- each case constructs a real, valid instance first,
    then tampers exactly one already-validated field."""
    # scan_store_for_canaries: `placement.encoding in ("literal", "fragmented")`.
    marker = generate_canary()
    ledger = CanaryPlacementLedger({("trace", "literal")})
    placement = PlacementRecord(marker.canary_id, "trace", "literal", "r1")
    object.__setattr__(placement, "encoding", "Literal")
    check(
        "FTF-0906-EXACT",
        "a case-tampered placement encoding is refused (wrapped as this "
        "module's own exception type), never silently folded to literal",
        _raises(
            InvalidEgressEvidenceError,
            scan_store_for_canaries,
            b"nothing secret here",
            {marker.canary_id: marker},
            (placement,),
            ledger,
        ),
    )

    # verify_egress: `entry.side in side_map` (duplicate-side detection).
    log_a = ConnectionLogEntry("f1", "client", "connected")
    log_b = ConnectionLogEntry("f1", "client", "refused")
    object.__setattr__(log_b, "side", "Client")
    plan0 = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    try:
        verify_egress(plan0, EgressEvidence(connection_logs=(log_a, log_b)))
        duplicate_raised = False
    except InvalidEgressEvidenceError:
        duplicate_raised = True
    check(
        "FTF-0906-EXACT",
        "two connection logs whose side differs only by case are distinct, not a duplicate",
        not duplicate_raised,
    )

    # verify_egress deny branch: `flow.expected == "deny"` (branch selector).
    allow1, deny1 = _flow("a1", "allow"), _flow("d1", "deny")
    plan1 = _plan((allow1, deny1))
    object.__setattr__(deny1, "expected", " deny")
    d_enf1, d_cl1, d_dl1 = _passing_deny("d1")
    r1 = verify_egress(plan1, EgressEvidence(enforcement=(d_enf1,), connection_logs=(d_cl1, d_dl1)))
    check(
        "FTF-0906-EXACT",
        "a case/whitespace-tampered flow.expected is never folded back into the deny branch",
        r1.flow_verdicts[1].status == "failed"
        and "vacuous_permitted_flow" in r1.flow_verdicts[1].codes,
    )

    # verify_egress deny branch: `enf.decision == "deny"`.
    allow2, deny2 = _flow("a1", "allow"), _flow("d1", "deny")
    plan2 = _plan((allow2, deny2))
    d_enf2, d_cl2, d_dl2 = _passing_deny("d1")
    object.__setattr__(d_enf2, "decision", "Deny")
    r2 = verify_egress(plan2, EgressEvidence(enforcement=(d_enf2,), connection_logs=(d_cl2, d_dl2)))
    check(
        "FTF-0906-EXACT",
        "a case-tampered enforcement decision is never folded back into a passing deny",
        r2.flow_verdicts[1].status == "failed"
        and "unauthorized_transfer" in r2.flow_verdicts[1].codes,
    )

    # verify_egress deny branch: `client_log.outcome != "connected"`.
    allow3, deny3 = _flow("a1", "allow"), _flow("d1", "deny")
    plan3 = _plan((allow3, deny3))
    d_enf3, d_cl3, d_dl3 = _passing_deny("d1")
    object.__setattr__(d_cl3, "outcome", " connected")
    r3 = verify_egress(plan3, EgressEvidence(enforcement=(d_enf3,), connection_logs=(d_cl3, d_dl3)))
    check(
        "FTF-0906-EXACT",
        "a whitespace-corrupted client outcome is compared exactly, not normalized to 'connected'",
        r3.flow_verdicts[1].status == "passed",
    )

    # verify_egress deny branch: `dest_log.outcome == "not_seen"`.
    allow4, deny4 = _flow("a1", "allow"), _flow("d1", "deny")
    plan4 = _plan((allow4, deny4))
    d_enf4, d_cl4, d_dl4 = _passing_deny("d1")
    object.__setattr__(d_dl4, "outcome", "Not_Seen")
    r4 = verify_egress(plan4, EgressEvidence(enforcement=(d_enf4,), connection_logs=(d_cl4, d_dl4)))
    check(
        "FTF-0906-EXACT",
        "a case-tampered destination outcome is never folded back into a passing deny",
        r4.flow_verdicts[1].status == "failed"
        and "unauthorized_transfer" in r4.flow_verdicts[1].codes,
    )

    # verify_egress allow branch: `insp.store == "controlled_destination"` (arrival lookup).
    allow5, deny5 = _flow("a1", "allow"), _flow("d1", "deny")
    plan5 = _plan((allow5, deny5))
    a_enf5, a_logs5, a_insp5 = _passing_allow("a1")
    object.__setattr__(a_insp5, "store", "Controlled_Destination")
    r5 = verify_egress(
        plan5,
        EgressEvidence(
            enforcement=(a_enf5,), connection_logs=a_logs5, plaintext_inspections=(a_insp5,)
        ),
    )
    check(
        "FTF-0906-EXACT",
        "a case-tampered inspection store is never matched as the controlled destination",
        r5.flow_verdicts[0].status == "failed"
        and "vacuous_permitted_flow" in r5.flow_verdicts[0].codes,
    )

    # verify_egress allow branch: `dest_log.outcome == "connected"` (arrival check).
    allow6, deny6 = _flow("a1", "allow"), _flow("d1", "deny")
    plan6 = _plan((allow6, deny6))
    a_enf6, a_logs6, a_insp6 = _passing_allow("a1")
    object.__setattr__(a_logs6[1], "outcome", "Connected")
    r6 = verify_egress(
        plan6,
        EgressEvidence(
            enforcement=(a_enf6,), connection_logs=a_logs6, plaintext_inspections=(a_insp6,)
        ),
    )
    check(
        "FTF-0906-EXACT",
        "a case-tampered destination connection outcome is never treated as a real arrival",
        r6.flow_verdicts[0].status == "failed"
        and "vacuous_permitted_flow" in r6.flow_verdicts[0].codes,
    )

    # verify_egress allow branch: `insp.store != "controlled_destination"`
    # (leak-elsewhere exclusion).
    allow7, deny7 = _flow("a1", "allow"), _flow("d1", "deny")
    plan7 = _plan((allow7, deny7))
    a_enf7, a_logs7, a_insp7 = _passing_allow("a1")
    leak_insp7 = PlaintextInspection(
        store="application_log",
        store_id="leak",
        flow_id="a1",
        inspected=True,
        decrypted=False,
        canary_found=True,
    )
    object.__setattr__(leak_insp7, "store", "Controlled_Destination")
    r7 = verify_egress(
        plan7,
        EgressEvidence(
            enforcement=(a_enf7,),
            connection_logs=a_logs7,
            plaintext_inspections=(a_insp7, leak_insp7),
        ),
    )
    check(
        "FTF-0906-EXACT",
        "a leak at a store spelled as a case-variant of controlled_destination "
        "is never excluded from the leak scan",
        r7.flow_verdicts[0].status == "failed" and "canary_leaked" in r7.flow_verdicts[0].codes,
    )


def test_aggregate_overall_status_is_exact_not_normalized() -> None:
    # `FlowVerdict.status` carries no dataclass-level validation of its own
    # (it is always one of this module's own lowercase literals in real
    # use, assigned internally by `verify_egress`) -- the aggregator must
    # not silently fold a malformed value into `failed`/`incomplete`.
    failed_variant = (FlowVerdict(flow_id="x", status="Failed", codes=()),)
    check(
        "FTF-0906-EXACT",
        "a non-canonical 'Failed' verdict status is not folded into the overall failed status",
        _aggregate_overall_status(failed_variant, frozenset(), ()) == "passed",
    )
    incomplete_variant = (FlowVerdict(flow_id="x", status="Incomplete", codes=()),)
    check(
        "FTF-0906-EXACT",
        "a non-canonical 'Incomplete' verdict status is not folded into "
        "the overall incomplete status",
        _aggregate_overall_status(incomplete_variant, frozenset(), ()) == "passed",
    )
    check(
        "FTF-0906-EXACT",
        "a real failed verdict still forces the overall status to failed",
        _aggregate_overall_status(
            (FlowVerdict(flow_id="x", status="failed", codes=()),), frozenset(), ()
        )
        == "failed",
    )


# --- verify_egress: the full rule set ---------------------------------------


def _passing_deny(flow_id: str = "d1") -> tuple:
    return (
        EnforcementObservation(flow_id, "process_guard", "deny", "blocked"),
        ConnectionLogEntry(flow_id, "client", "refused"),
        ConnectionLogEntry(flow_id, "destination", "not_seen"),
    )


def _passing_allow(flow_id: str = "a1") -> tuple:
    enforcement = EnforcementObservation(flow_id, "process_guard", "allow", "ok")
    logs = (
        ConnectionLogEntry(flow_id, "client", "connected"),
        ConnectionLogEntry(flow_id, "destination", "connected"),
    )
    inspection = PlaintextInspection(
        store="controlled_destination",
        store_id="sink-A",
        flow_id=flow_id,
        inspected=True,
        decrypted=False,
    )
    return enforcement, logs, inspection


_DEST_AND_HISTORY = frozenset({"controlled_destination", "workflow_history"})


def test_fully_exercised_plan_reports_passed() -> None:
    allow_flow = _flow("a1", "allow")
    deny_flow = _flow("d1", "deny")
    plan = _plan((allow_flow, deny_flow), required_stores=_DEST_AND_HISTORY)
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    history_insp = PlaintextInspection(
        store="workflow_history", store_id="wf-1", flow_id=None, inspected=True, decrypted=False
    )
    evidence = EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(a_insp, history_insp),
    )
    report = verify_egress(plan, evidence)
    check("FTF-0906-ACC1", "allow flow passes", report.flow_verdicts[0].status == "passed")
    check("FTF-0906-ACC1", "deny flow passes", report.flow_verdicts[1].status == "passed")
    check(
        "FTF-0906-ACC1",
        "overall status is passed when workflow_history was exercised",
        report.status == "passed",
    )
    check("FTF-0906-ACC1", "report_sha256 is a 64-hex digest", len(report.report_sha256) == 64)


def test_missing_workflow_history_forces_partial_never_passed() -> None:
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")), required_stores=_DEST_AND_HISTORY)
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    evidence = EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(a_insp,),
    )
    report = verify_egress(plan, evidence)
    check(
        "FTF-0906-SCOPE",
        "every flow still passes on its own",
        all(v.status == "passed" for v in report.flow_verdicts),
    )
    check(
        "FTF-0906-SCOPE",
        "overall status is partial, never passed, when workflow_history is not exercised",
        report.status == "partial" and report.not_exercised == ("workflow_history",),
    )


def test_missing_connection_logs_is_incomplete() -> None:
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    d_enf, d_c_log, d_d_log = _passing_deny()
    evidence = EgressEvidence(enforcement=(d_enf,), connection_logs=(d_c_log, d_d_log))
    report = verify_egress(plan, evidence)
    allow_verdict = next(v for v in report.flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-RULE",
        "a flow with no evidence at all is incomplete with connection_log_missing",
        allow_verdict.status == "incomplete" and allow_verdict.codes == ("connection_log_missing",),
    )
    check("FTF-0906-RULE", "overall rolls up to incomplete", report.status == "incomplete")


def test_encrypted_capture_alone_is_insufficient_for_either_class() -> None:
    for flow_id, builder in (("a1", _passing_allow), ("d1", _passing_deny)):
        plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
        built = builder(flow_id)
        enforcement = (built[0],)
        logs = built[1] if flow_id == "a1" else built[1:]
        capture = PacketCaptureObservation(flow_id=flow_id, encrypted=True, canary_found=False)
        evidence = EgressEvidence(
            enforcement=enforcement, connection_logs=logs, packet_captures=(capture,)
        )
        report = verify_egress(plan, evidence)
        verdict = next(v for v in report.flow_verdicts if v.flow_id == flow_id)
        check(
            "FTF-0906-NEG1",
            f"an encrypted-only capture with no plaintext inspection is incomplete ({flow_id})",
            verdict.status == "incomplete" and verdict.codes == ("encrypted_capture_insufficient",),
        )


def test_deny_flow_fails_on_each_broken_condition() -> None:
    marker = generate_canary()
    base_plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    scenarios = {
        "enforcement allowed instead of denied": EgressEvidence(
            enforcement=(EnforcementObservation("d1", "process_guard", "allow", "miscategorized"),),
            connection_logs=(
                ConnectionLogEntry("d1", "client", "refused"),
                ConnectionLogEntry("d1", "destination", "not_seen"),
            ),
        ),
        "client actually connected": EgressEvidence(
            enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
            connection_logs=(
                ConnectionLogEntry("d1", "client", "connected"),
                ConnectionLogEntry("d1", "destination", "not_seen"),
            ),
        ),
        "destination actually saw the connection": EgressEvidence(
            enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
            connection_logs=(
                ConnectionLogEntry("d1", "client", "refused"),
                ConnectionLogEntry("d1", "destination", "connected"),
            ),
        ),
        "canary still turned up in an inspected store": EgressEvidence(
            enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
            connection_logs=(
                ConnectionLogEntry("d1", "client", "refused"),
                ConnectionLogEntry("d1", "destination", "not_seen"),
            ),
            plaintext_inspections=(
                PlaintextInspection(
                    store="error_path",
                    store_id="log-1",
                    flow_id="d1",
                    inspected=True,
                    decrypted=False,
                    canary_found=True,
                ),
            ),
        ),
    }
    for description, partial_evidence in scenarios.items():
        allow_enf, allow_logs, allow_insp = _passing_allow()
        evidence = EgressEvidence(
            enforcement=(allow_enf, *partial_evidence.enforcement),
            connection_logs=(*allow_logs, *partial_evidence.connection_logs),
            plaintext_inspections=(allow_insp, *partial_evidence.plaintext_inspections),
        )
        report = verify_egress(base_plan, evidence)
        verdict = next(v for v in report.flow_verdicts if v.flow_id == "d1")
        check(
            "FTF-0906-RULE",
            f"deny flow fails unauthorized_transfer: {description}",
            verdict.status == "failed" and verdict.codes == ("unauthorized_transfer",),
        )
    check(
        "FTF-0906-SMOKE", "marker fixture is usable", marker.value.startswith("CANARY-DO-NOT-USE-")
    )


def test_allow_flow_vacuous_without_destination_arrival() -> None:
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    d_enf, d_c_log, d_d_log = _passing_deny()
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "allow", "ok"), d_enf),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            d_c_log,
            d_d_log,
        ),
        # No PlaintextInspection at controlled_destination for a1: never actually
        # proves arrival, even though the connection log alone looks fine. An
        # inspection at an unrelated store (inspected=True) is deliberately
        # present too, so a weakened "store == X OR inspected" positive-control
        # check (instead of AND) would wrongly treat this one as the proof.
        plaintext_inspections=(
            PlaintextInspection("application_log", "client-log", "a1", True, False),
        ),
    )
    report = verify_egress(plan, evidence)
    verdict = next(v for v in report.flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-RULE",
        "an allow flow with no positive-control inspection is vacuous_permitted_flow",
        verdict.status == "failed" and verdict.codes == ("vacuous_permitted_flow",),
    )


def test_allow_flow_canary_leaked_outside_authorized_sinks() -> None:
    placement = PlacementRecord(
        canary_id=generate_canary().canary_id, sink="trace", encoding="literal", authorized_by="r1"
    )
    flow = _flow("a1", "allow", placements=(placement,))
    plan = _plan((flow, _flow("d1", "deny")))
    d_enf, d_c_log, d_d_log = _passing_deny()
    dest_insp = PlaintextInspection(
        store="controlled_destination",
        store_id="sink-A",
        flow_id="a1",
        inspected=True,
        decrypted=False,
    )
    leaking_insp = PlaintextInspection(
        store="error_path",
        store_id="log-1",
        flow_id="a1",
        inspected=True,
        decrypted=False,
        canary_found=True,
    )
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "allow", "ok"), d_enf),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            d_c_log,
            d_d_log,
        ),
        plaintext_inspections=(dest_insp, leaking_insp),
    )
    report = verify_egress(plan, evidence)
    verdict = next(v for v in report.flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-RULE",
        "a canary in a store the flow never authorized is canary_leaked",
        verdict.status == "failed" and verdict.codes == ("canary_leaked",),
    )


def test_allow_flow_canary_in_authorized_sink_is_not_a_leak() -> None:
    placement = PlacementRecord(
        canary_id=generate_canary().canary_id,
        sink="error_path",
        encoding="literal",
        authorized_by="r1",
    )
    flow = _flow("a1", "allow", placements=(placement,))
    plan = _plan((flow, _flow("d1", "deny")))
    d_enf, d_c_log, d_d_log = _passing_deny()
    dest_insp = PlaintextInspection(
        store="controlled_destination",
        store_id="sink-A",
        flow_id="a1",
        inspected=True,
        decrypted=False,
    )
    authorized_insp = PlaintextInspection(
        store="error_path",
        store_id="log-1",
        flow_id="a1",
        inspected=True,
        decrypted=False,
        canary_found=True,
    )
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "allow", "ok"), d_enf),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            d_c_log,
            d_d_log,
        ),
        plaintext_inspections=(dest_insp, authorized_insp),
    )
    report = verify_egress(plan, evidence)
    verdict = next(v for v in report.flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-RULE",
        "a canary in a store this flow's own placements authorize is not a leak",
        verdict.status == "passed",
    )


def test_inspection_coverage_missing_when_a_required_store_is_never_inspected() -> None:
    plan = _plan(
        (_flow("a1", "allow"), _flow("d1", "deny")),
        required_stores=frozenset({"controlled_destination", "artifact", "workflow_history"}),
    )
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    evidence = EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(a_insp,),  # "artifact" never inspected
    )
    report = verify_egress(plan, evidence)
    check(
        "FTF-0906-RULE",
        "a required store never inspected is reported as inspection_coverage_missing",
        "inspection_coverage_missing" in report.codes,
    )
    check("FTF-0906-RULE", "that rolls up to overall incomplete", report.status == "incomplete")


def test_any_failed_flow_makes_the_whole_report_failed() -> None:
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    a_enf, a_logs, a_insp = _passing_allow()
    evidence = EgressEvidence(
        enforcement=(a_enf,),  # d1 has no evidence at all -> incomplete, not failed
        connection_logs=a_logs,
        plaintext_inspections=(a_insp,),
    )
    report = verify_egress(plan, evidence)
    check(
        "FTF-0906-ROLLUP",
        "a merely incomplete flow does not force overall failed",
        report.status == "incomplete",
    )


# --- scan_store_for_canaries -------------------------------------------------


def test_scan_finds_every_declared_encoding() -> None:
    from packages.security.canary_secret_generator import encode_canary, fragment_canary

    for encoding in ("literal", "base64", "hex", "url"):
        marker2 = generate_canary()
        ledger2 = CanaryPlacementLedger({("trace", encoding)})
        placement2 = PlacementRecord(marker2.canary_id, "trace", encoding, "r1")
        ledger2.record_placement(placement2)
        needle = encode_canary(marker2, encoding)
        try:
            scan_store_for_canaries(
                f"prefix {needle} suffix".encode(),
                {marker2.canary_id: marker2},
                (placement2,),
                ledger2,
            )
            raised = False
        except CanaryLeakedError:
            raised = True
        check(
            "FTF-0906-SCAN",
            f"a real {encoding} canary raises CanaryLeakedError immediately",
            raised,
        )

    frag_marker = generate_canary()
    ledger3 = CanaryPlacementLedger({("trace", "fragmented")})
    placement3 = PlacementRecord(frag_marker.canary_id, "trace", "fragmented", "r1")
    ledger3.record_placement(placement3)
    pieces = fragment_canary(frag_marker, 2)
    reassembled = "".join(pieces)
    try:
        scan_store_for_canaries(
            f"{reassembled}".encode(), {frag_marker.canary_id: frag_marker}, (placement3,), ledger3
        )
        raised = False
    except CanaryLeakedError:
        raised = True
    check("FTF-0906-SCAN", "a reassembled fragmented canary raises CanaryLeakedError", raised)


def test_scan_clean_data_does_not_raise_and_marks_checked() -> None:
    marker = generate_canary()
    ledger = CanaryPlacementLedger({("trace", "literal")})
    placement = PlacementRecord(marker.canary_id, "trace", "literal", "r1")
    ledger.record_placement(placement)
    found = scan_store_for_canaries(
        b"nothing secret here", {marker.canary_id: marker}, (placement,), ledger
    )
    check("FTF-0906-SCAN", "clean data returns an empty found set", found == frozenset())
    try:
        ledger.assert_leak_test_complete()
        complete = True
    except Exception:
        complete = False
    check("FTF-0906-SCAN", "ledger reports complete after a clean checked scan", complete)


def test_scan_rejects_unknown_canary_id_and_bad_types() -> None:
    marker = generate_canary()
    ledger = CanaryPlacementLedger({("trace", "literal")})
    other_marker = generate_canary()
    foreign_placement = PlacementRecord(other_marker.canary_id, "trace", "literal", "r1")
    check(
        "FTF-0906-SCAN",
        "a placement naming a canary id absent from `markers` is refused",
        _raises(
            InvalidEgressEvidenceError,
            scan_store_for_canaries,
            b"data",
            {marker.canary_id: marker},
            (foreign_placement,),
            ledger,
        ),
    )
    check(
        "FTF-0906-SCAN",
        "non-bytes data is refused",
        _raises(InvalidEgressEvidenceError, scan_store_for_canaries, "not-bytes", {}, (), ledger),
    )


# --- Fault injection (Law 1): mutate the real module, prove each guard is load-bearing. ---

MODULE = ROOT / "packages" / "security" / "egress_capture_verification.py"

_PROBE = """
import sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {root!r} + "/apps/api")
from packages.security.egress_capture_verification import (
    EgressPlan, PlannedFlow, EnforcementObservation, ConnectionLogEntry,
    PlaintextInspection, PacketCaptureObservation, EgressEvidence,
    EgressCaptureVerificationError, verify_egress,
)

DIGEST = "a" * 64

def flow(flow_id, expected, placements=()):
    return PlannedFlow(
        flow_id=flow_id, surface="data", destination_label="d",
        destination_class="external" if expected == "deny" else "internal",
        expected=expected, canary_placements=placements,
    )

def outcome(fn):
    try:
        return fn()
    except EgressCaptureVerificationError as exc:
        return exc.code
    except Exception as exc:
        return "ERROR:" + type(exc).__name__

def no_allow_or_deny():
    EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST, flows=(flow("a1", "allow"),))

def connection_log_missing():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
        connection_logs=(ConnectionLogEntry("d1", "client", "refused"),
                          ConnectionLogEntry("d1", "destination", "not_seen")),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "a1")

def encrypted_only():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
        connection_logs=(ConnectionLogEntry("d1", "client", "refused"),
                          ConnectionLogEntry("d1", "destination", "not_seen")),
        packet_captures=(PacketCaptureObservation("d1", encrypted=True, canary_found=False),),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "d1")

def deny_leaked_canary():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),
                      EnforcementObservation("a1", "process_guard", "allow", "ok")),
        connection_logs=(
            ConnectionLogEntry("d1", "client", "refused"),
            ConnectionLogEntry("d1", "destination", "not_seen"),
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
        ),
        plaintext_inspections=(
            PlaintextInspection("error_path", "log-1", "d1", True, False, canary_found=True),
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False),
        ),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "d1")

def allow_vacuous():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "allow", "ok"),
                      EnforcementObservation("d1", "process_guard", "deny", "blocked")),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            ConnectionLogEntry("d1", "client", "refused"),
            ConnectionLogEntry("d1", "destination", "not_seen"),
        ),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "a1")

def coverage_missing():
    stores = frozenset({{"controlled_destination", "artifact", "workflow_history"}})
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")),
                       required_stores=stores)
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "allow", "ok"),
                      EnforcementObservation("d1", "process_guard", "deny", "blocked")),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            ConnectionLogEntry("d1", "client", "refused"),
            ConnectionLogEntry("d1", "destination", "not_seen"),
        ),
        plaintext_inspections=(
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False),
        ),
    )
    report = verify_egress(plan, evidence)
    return report.status

def history_never_passes():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")),
                       required_stores=frozenset({{"controlled_destination", "workflow_history"}}))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "allow", "ok"),
                      EnforcementObservation("d1", "process_guard", "deny", "blocked")),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            ConnectionLogEntry("d1", "client", "refused"),
            ConnectionLogEntry("d1", "destination", "not_seen"),
        ),
        plaintext_inspections=(
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False),
        ),
    )
    report = verify_egress(plan, evidence)
    return report.status

print(outcome(no_allow_or_deny))
print(outcome(connection_log_missing))
print(outcome(encrypted_only))
print(outcome(deny_leaked_canary))
print(outcome(allow_vacuous))
print(outcome(coverage_missing))
print(outcome(history_never_passes))
"""


def _probe(source: str | None) -> list[str]:
    original = MODULE.read_bytes()
    try:
        if source is not None:
            MODULE.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", _PROBE.format(root=str(ROOT))],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        MODULE.write_bytes(original)
    if MODULE.read_bytes() != original:
        raise RuntimeError("module not restored")
    return (
        result.stdout.strip().split("\n")
        if result.returncode == 0
        else ["ERROR:" + result.stderr[-300:]]
    )


BASELINE = [
    "flow_class_missing",
    "incomplete",
    "incomplete",
    "failed",
    "failed",
    "incomplete",
    "partial",
]

# (description, regex, replacement, output line index that must change)
MUTATIONS = [
    (
        "flow-class-missing check removed (an allow-only/deny-only plan is accepted)",
        r'if "allow" not in expected_decisions or "deny" not in expected_decisions:',
        "if False:",
        0,
    ),
    (
        "connection_log_missing branch removed",
        r"if enf is None or client_log is None or dest_log is None:",
        "if False:",
        1,
    ),
    (
        "encrypted-capture-insufficient branch removed",
        r"elif capture is not None and capture\.encrypted and not flow_inspections:",
        "elif False:",
        2,
    ),
    (
        "deny-flow canary check dropped (a leak at an inspected store is not seen)",
        r"and not canary_seen\n",
        "\n",
        3,
    ),
    (
        "allow-flow vacuous check removed (no positive control required)",
        r"if not arrived:\n"
        r'                status = "failed"\n'
        r'                codes\.append\("vacuous_permitted_flow"\)',
        'if False:\n                status = "failed"\n'
        '                codes.append("vacuous_permitted_flow")',
        4,
    ),
    (
        "required-store coverage no longer forces overall incomplete",
        r"if missing_required or any\(v\.status == \"incomplete\" " r"for v in flow_verdicts\):",
        'if any(v.status == "incomplete" for v in flow_verdicts):',
        5,
    ),
    (
        "workflow_history not_exercised/partial forcing removed",
        r'if "workflow_history" in plan\.required_stores and '
        r'"workflow_history" not in inspected_stores:',
        "if False:",
        6,
    ),
]


def test_fault_injection_each_rule_is_load_bearing() -> None:
    check("FTF-0906-FAULT", "unmutated probe matches baseline", _probe(None) == BASELINE)
    text = MODULE.read_text(encoding="utf-8")
    for description, pattern, replacement, index in MUTATIONS:
        mutated, count = re.subn(pattern, replacement, text, count=1)
        check("FTF-0906-FAULT", f"mutation pattern matches once: {description}", count == 1)
        if count != 1:
            continue
        out = _probe(mutated)
        check(
            "FTF-0906-FAULT",
            f"mutation turns the probe RED: {description}",
            len(out) == len(BASELINE) and out[index] != BASELINE[index],
        )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print(f"FTF-0906 Egress capture/verification tests: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)
    print("FTF-0906 Egress capture/verification tests: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
