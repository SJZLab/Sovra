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

import os
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
    _MANDATORY_REQUIRED_STORES,
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
    _needles_for_encoding,
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
_PERCENT_HEX_PATTERN = re.compile(r"%[0-9a-fA-F]{2}")


def _PERCENT_LOWER(value: str) -> str:  # noqa: N802
    return _PERCENT_HEX_PATTERN.sub(lambda m: m.group(0).lower(), value)


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
        "required_stores as a plain set instead of a frozenset is refused "
        "(a set that otherwise satisfies every mandatory-store requirement, so this "
        "isolates the frozenset-type check from the mandatory-log-store check)",
        _raises(
            InvalidEgressPlanError,
            _plan,
            good_flows,
            {"controlled_destination", "workflow_history", *_MANDATORY_REQUIRED_STORES},
        ),
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
        required_stores=_DEST_AND_HISTORY,
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
            PlaintextInspection("controlled_destination", "sink", "Flow-A", True, False, True),
            PlaintextInspection("workflow_history", "wf", None, True, False),
            *_clean_mandatory_store_inspections(),
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
    all_passed = (FlowVerdict(flow_id="x", status="passed", codes=()),)
    check(
        "FTF-0906-UNATTR",
        "unattributed_leak forces failed even when every flow verdict passed",
        _aggregate_overall_status(all_passed, frozenset(), (), unattributed_leak=True) == "failed",
    )
    check(
        "FTF-0906-UNPLANNED",
        "unplanned_evidence forces failed even when every flow verdict passed",
        _aggregate_overall_status(all_passed, frozenset(), (), unplanned_evidence=True) == "failed",
    )


# --- CALLER-NARROWED-COVERAGE: required_stores cannot omit log stores ------


def test_required_stores_cannot_omit_mandatory_log_stores() -> None:
    good_flows = (_flow("a1", "allow"), _flow("d1", "deny"))
    check(
        "FTF-0906-COVERAGE",
        "a required_stores set omitting every log store is refused at construction",
        _raises(InvalidEgressPlanError, _plan, good_flows, frozenset({"controlled_destination"})),
    )
    check(
        "FTF-0906-COVERAGE",
        "a required_stores set omitting just one mandatory log store is refused",
        _raises(
            InvalidEgressPlanError,
            _plan,
            good_flows,
            frozenset({"controlled_destination", "application_log", "artifact"}),
        ),
    )
    check(
        "FTF-0906-COVERAGE",
        "a required_stores set carrying every mandatory log store constructs fine",
        bool(_plan(good_flows, frozenset({"controlled_destination", *_MANDATORY_REQUIRED_STORES}))),
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
    # canary_found=True: the positive control Section 12 requires -- the
    # canary really arrived at the controlled destination, not merely "an
    # inspection record happened to exist" (VACUOUS-POSITIVE-CONTROL).
    inspection = PlaintextInspection(
        store="controlled_destination",
        store_id="sink-A",
        flow_id=flow_id,
        inspected=True,
        decrypted=False,
        canary_found=True,
    )
    return enforcement, logs, inspection


def _clean_mandatory_store_inspections(flow_id: str | None = None) -> tuple:
    """A clean (canary_found=False), inspected=True record for each of the
    three mandatory log stores (CALLER-NARROWED-COVERAGE) -- the fixture
    every test that asserts `report.status` now needs, since those three
    stores can no longer be silently left out of coverage."""
    return tuple(
        PlaintextInspection(store, f"{store}-clean", flow_id, True, decrypted=False)
        for store in ("application_log", "error_path", "artifact")
    )


_DEST_AND_HISTORY = frozenset(
    {"controlled_destination", "workflow_history", *_MANDATORY_REQUIRED_STORES}
)


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
        plaintext_inspections=(a_insp, history_insp, *_clean_mandatory_store_inspections()),
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
        plaintext_inspections=(a_insp, *_clean_mandatory_store_inspections()),
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
        canary_found=True,
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
        canary_found=True,
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


def test_allow_flow_enforcement_client_destination_must_agree() -> None:
    # ALLOW-FLOW-IGNORES-ENFORCEMENT, the finding's own literal scenario:
    # enforcement says deny and the client log agrees (refused), but the
    # destination log claims connected anyway -- inconsistent evidence
    # must never be read as a real arrival just because the destination
    # side alone looks fine.
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    d_enf, d_c_log, d_d_log = _passing_deny()
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "deny", "blocked"), d_enf),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "refused"),
            ConnectionLogEntry("a1", "destination", "connected"),
            d_c_log,
            d_d_log,
        ),
        plaintext_inspections=(
            PlaintextInspection(
                "controlled_destination", "sink-A", "a1", True, decrypted=False, canary_found=True
            ),
        ),
    )
    report = verify_egress(plan, evidence)
    verdict = next(v for v in report.flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-ENFORCE",
        "enforcement=deny + client=refused with a contradicting destination=connected "
        "is never read as a real arrival",
        verdict.status == "failed" and verdict.codes == ("vacuous_permitted_flow",),
    )


def test_encrypted_capture_is_not_satisfied_by_an_uninspected_dummy_record() -> None:
    # ENCRYPTED-CAPTURE-EVADABLE: a PlaintextInspection that was never
    # actually inspected must not count as "something to corroborate" an
    # encrypted capture -- only a real, inspected record does.
    for flow_id, builder in (("a1", _passing_allow), ("d1", _passing_deny)):
        plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
        built = builder(flow_id)
        enforcement = (built[0],)
        logs = built[1] if flow_id == "a1" else built[1:]
        capture = PacketCaptureObservation(flow_id=flow_id, encrypted=True, canary_found=False)
        dummy_uninspected = PlaintextInspection(
            store="application_log",
            store_id="dummy",
            flow_id=flow_id,
            inspected=False,
            decrypted=False,
        )
        evidence = EgressEvidence(
            enforcement=enforcement,
            connection_logs=logs,
            plaintext_inspections=(dummy_uninspected,),
            packet_captures=(capture,),
        )
        report = verify_egress(plan, evidence)
        verdict = next(v for v in report.flow_verdicts if v.flow_id == flow_id)
        check(
            "FTF-0906-ENCRYPTED",
            f"an uninspected dummy inspection record cannot stand in for a real one ({flow_id})",
            verdict.status == "incomplete" and verdict.codes == ("encrypted_capture_insufficient",),
        )


def test_unattributed_leak_and_unplanned_evidence_fail_the_whole_report() -> None:
    # UNATTRIBUTED-LEAK-IGNORED / UNPLANNED-EGRESS-IGNORED, proven through
    # verify_egress itself, not only through the aggregator helper.
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    base_inspections = (a_insp, *_clean_mandatory_store_inspections())

    unattributed_insp = PlaintextInspection(
        "application_log", "shared-log", None, True, decrypted=False, canary_found=True
    )
    report_unattributed = verify_egress(
        plan,
        EgressEvidence(
            enforcement=(a_enf, d_enf),
            connection_logs=(*a_logs, d_c_log, d_d_log),
            plaintext_inspections=(*base_inspections, unattributed_insp),
        ),
    )
    check(
        "FTF-0906-UNATTR",
        "a canary found at a store never tied to any flow fails the whole report",
        report_unattributed.status == "failed"
        and "unattributed_canary_found" in report_unattributed.codes
        and all(v.status != "failed" for v in report_unattributed.flow_verdicts),
    )

    report_unplanned = verify_egress(
        plan,
        EgressEvidence(
            enforcement=(
                a_enf,
                d_enf,
                EnforcementObservation("ghost", "process_guard", "allow", "ok"),
            ),
            connection_logs=(*a_logs, d_c_log, d_d_log),
            plaintext_inspections=base_inspections,
        ),
    )
    check(
        "FTF-0906-UNPLANNED",
        "an enforcement observation for a flow_id this plan never declared fails the whole report",
        report_unplanned.status == "failed"
        and "unplanned_egress_evidence" in report_unplanned.codes
        and all(v.status != "failed" for v in report_unplanned.flow_verdicts),
    )


def test_unplanned_evidence_flow_id_match_is_exact_not_case_folded() -> None:
    # flow_id is free-form text (never `_member`-normalised -- see
    # test_distinct_case_flow_ids_are_never_merged_and_match_exactly), so
    # the UNPLANNED-EGRESS check's own `not in plan_flow_ids` match must
    # stay exact too: a mutant that case/whitespace-folds the evidence
    # side before that check would silently match "A1" (not a declared
    # flow) to the real "a1", hiding genuinely unplanned evidence. One
    # case per evidence kind, since each has its own `any(...)` clause.
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    base_inspections = (a_insp, *_clean_mandatory_store_inspections())

    def _build(**override: tuple) -> EgressEvidence:
        kwargs = {
            "enforcement": (a_enf, d_enf),
            "connection_logs": (*a_logs, d_c_log, d_d_log),
            "plaintext_inspections": base_inspections,
        }
        kwargs.update(override)
        return EgressEvidence(**kwargs)

    cases = {
        "enforcement": _build(
            enforcement=(a_enf, d_enf, EnforcementObservation("A1", "process_guard", "allow", "ok"))
        ),
        "connection_logs": _build(
            connection_logs=(
                *a_logs,
                d_c_log,
                d_d_log,
                ConnectionLogEntry("A1", "client", "connected"),
            )
        ),
        "packet_captures": _build(
            packet_captures=(PacketCaptureObservation("A1", encrypted=False, canary_found=False),)
        ),
        "plaintext_inspections": _build(
            plaintext_inspections=(
                *base_inspections,
                PlaintextInspection("artifact", "s", "A1", True, decrypted=False),
            )
        ),
    }
    for kind, evidence in cases.items():
        report = verify_egress(plan, evidence)
        check(
            "FTF-0906-UNPLANNED",
            f"a case-varied flow_id ('A1' vs the real 'a1') in {kind} is never folded into "
            "a known flow -- still flagged as unplanned egress",
            report.status == "failed" and "unplanned_egress_evidence" in report.codes,
        )


def test_report_binds_policy_digest_scope_and_inspected_stores() -> None:
    # REPORT-UNBOUND: the report carries (and binds into its own hash) the
    # exact plan it verified, plus which stores it actually inspected.
    plan = _plan(
        (_flow("a1", "allow"), _flow("d1", "deny")),
        required_stores=_DEST_AND_HISTORY,
    )
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    history_insp = PlaintextInspection(
        store="workflow_history", store_id="wf-1", flow_id=None, inspected=True, decrypted=False
    )
    evidence = EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(a_insp, history_insp, *_clean_mandatory_store_inspections()),
    )
    report = verify_egress(plan, evidence)
    check(
        "FTF-0906-REPORT",
        "report.policy_digest matches the plan",
        report.policy_digest == _DIGEST,
    )
    check("FTF-0906-REPORT", "report.scope matches the plan", report.scope == "process")
    check(
        "FTF-0906-REPORT",
        "report.inspected_stores lists every store that was actually inspected",
        set(report.inspected_stores)
        == {"controlled_destination", "workflow_history", *_MANDATORY_REQUIRED_STORES},
    )
    other_plan = _plan(
        (_flow("a1", "allow"), _flow("d1", "deny")), required_stores=_DEST_AND_HISTORY
    )
    object.__setattr__(other_plan, "policy_digest", "b" * 64)
    other_report = verify_egress(other_plan, evidence)
    check(
        "FTF-0906-REPORT",
        "changing only the plan's policy_digest changes report_sha256",
        other_report.report_sha256 != report.report_sha256,
    )


def test_inspection_coverage_missing_when_a_required_store_is_never_inspected() -> None:
    plan = _plan(
        (_flow("a1", "allow"), _flow("d1", "deny")),
        required_stores=frozenset(
            {"controlled_destination", "workflow_history", *_MANDATORY_REQUIRED_STORES}
        ),
    )
    a_enf, a_logs, a_insp = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    other_mandatory = tuple(
        PlaintextInspection(store, f"{store}-clean", None, True, decrypted=False)
        for store in _MANDATORY_REQUIRED_STORES
        if store != "artifact"
    )
    evidence = EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(a_insp, *other_mandatory),  # "artifact" never inspected
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


def test_scan_finds_case_variant_hex_and_url_encodings() -> None:
    # A real log/header dump or intermediary proxy does not always
    # preserve the exact casing `bytes.hex()`/`quote()` produce -- upper-
    # cased hex and lower-cased percent-escapes are real, found variants
    # (the finding's own "upper-case hex / percent-encoded forms" gap),
    # not a theoretical one. `_needles_for_encoding` itself is checked
    # directly first, then the full scan.
    from urllib.parse import quote

    from packages.security.canary_secret_generator import CanaryMarker

    hex_marker = generate_canary()
    canonical_hex = hex_marker.value.encode().hex()
    hex_needles = _needles_for_encoding(hex_marker, "hex")
    check(
        "FTF-0906-SCAN",
        "_needles_for_encoding returns the upper-case hex variant too",
        canonical_hex in hex_needles and canonical_hex.upper() in hex_needles,
    )
    ledger_hex = CanaryPlacementLedger({("trace", "hex")})
    placement_hex = PlacementRecord(hex_marker.canary_id, "trace", "hex", "r1")
    ledger_hex.record_placement(placement_hex)
    check(
        "FTF-0906-SCAN",
        "an upper-cased hex-encoded canary in real data still raises CanaryLeakedError",
        _raises(
            CanaryLeakedError,
            scan_store_for_canaries,
            f"leaked: {canonical_hex.upper()}".encode(),
            {hex_marker.canary_id: hex_marker},
            (placement_hex,),
            ledger_hex,
        ),
    )

    # A synthetic marker whose value needs real percent-escaping (unlike a
    # real generate_canary() token, which never contains a byte quote()
    # escapes) -- only to exercise the url-encoding case-variant path.
    url_marker = CanaryMarker("canary-url-case-test", "CANARY-DO-NOT-USE-ab;cd")
    canonical_url = quote(url_marker.value)
    url_needles = _needles_for_encoding(url_marker, "url")
    check(
        "FTF-0906-SCAN",
        "_needles_for_encoding returns a lower-cased percent-escape variant when one exists",
        canonical_url in url_needles
        and canonical_url != canonical_url.lower()
        and any(n == _PERCENT_LOWER(canonical_url) for n in url_needles),
    )
    ledger_url = CanaryPlacementLedger({("trace", "url")})
    placement_url = PlacementRecord(url_marker.canary_id, "trace", "url", "r1")
    ledger_url.record_placement(placement_url)
    lower_percent_url = _PERCENT_LOWER(canonical_url)
    check(
        "FTF-0906-SCAN",
        "a lower-cased percent-escaped canary in real data still raises CanaryLeakedError",
        _raises(
            CanaryLeakedError,
            scan_store_for_canaries,
            f"leaked: {lower_percent_url}".encode(),
            {url_marker.canary_id: url_marker},
            (placement_url,),
            ledger_url,
        ),
    )


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


# --- Round 3: HISTORY-OPTIONAL, CLIENT-UNOBSERVED and the unpinned clauses ---


def _clean_all_but_history(flow_ids: tuple[str, str] = ("a1", "d1")) -> EgressEvidence:
    a_enf, a_logs, a_insp = _passing_allow(flow_ids[0])
    d_enf, d_c_log, d_d_log = _passing_deny(flow_ids[1])
    return EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(a_insp, *_clean_mandatory_store_inspections()),
    )


def test_history_is_always_partial_whatever_required_stores_says() -> None:
    # HISTORY-OPTIONAL: a caller that leaves workflow_history out of
    # `required_stores` must still never get `passed` without history
    # evidence. Runs the real default code with the attack input present.
    no_history = frozenset({"controlled_destination", *_MANDATORY_REQUIRED_STORES})
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")), required_stores=no_history)
    report = verify_egress(plan, _clean_all_but_history())
    check(
        "FTF-0906-R3-1",
        "required_stores without workflow_history still reports partial, never passed",
        report.status == "partial" and report.not_exercised == ("workflow_history",),
    )
    history = PlaintextInspection("workflow_history", "wf-1", None, True, decrypted=False)
    evidence = _clean_all_but_history()
    with_history = EgressEvidence(
        enforcement=evidence.enforcement,
        connection_logs=evidence.connection_logs,
        plaintext_inspections=(*evidence.plaintext_inspections, history),
    )
    check(
        "FTF-0906-R3-1",
        "real workflow_history evidence still lets the same narrowed plan pass",
        verify_egress(plan, with_history).status == "passed",
    )
    uninspected = PlaintextInspection("workflow_history", "wf-1", None, False, decrypted=False)
    not_looked_at = EgressEvidence(
        enforcement=evidence.enforcement,
        connection_logs=evidence.connection_logs,
        plaintext_inspections=(*evidence.plaintext_inspections, uninspected),
    )
    check(
        "FTF-0906-R3-1",
        "an uninspected workflow_history record is not history evidence",
        verify_egress(plan, not_looked_at).status == "partial",
    )


def test_unobserved_on_any_side_is_incomplete() -> None:
    # CLIENT-UNOBSERVED-PASSES (Law 17): `unobserved` on the client side, the
    # destination side or both is never a pass and never a fabricated verdict.
    for flow_id, expected in (("d1", "deny"), ("a1", "allow")):
        for client_outcome, dest_outcome, codes in (
            ("unobserved", "not_seen", ("client_unobserved",)),
            ("refused", "unobserved", ("destination_unobserved",)),
            ("unobserved", "unobserved", ("destination_unobserved", "client_unobserved")),
        ):
            other = "a1" if flow_id == "d1" else "d1"
            plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
            base = _clean_all_but_history()
            logs = tuple(entry for entry in base.connection_logs if entry.flow_id == other)
            enforcement = tuple(o for o in base.enforcement if o.flow_id == other)
            if expected == "deny":
                enf = EnforcementObservation(flow_id, "process_guard", "deny", "blocked")
            else:
                enf = EnforcementObservation(flow_id, "process_guard", "allow", "ok")
            evidence = EgressEvidence(
                enforcement=(*enforcement, enf),
                connection_logs=(
                    *logs,
                    ConnectionLogEntry(flow_id, "client", client_outcome),
                    ConnectionLogEntry(flow_id, "destination", dest_outcome),
                ),
                plaintext_inspections=base.plaintext_inspections,
            )
            report = verify_egress(plan, evidence)
            verdict = next(v for v in report.flow_verdicts if v.flow_id == flow_id)
            check(
                "FTF-0906-R3-2",
                f"{expected} flow client={client_outcome} dest={dest_outcome} is incomplete "
                f"with {codes}",
                verdict.status == "incomplete" and verdict.codes == codes,
            )
            check(
                "FTF-0906-R3-2",
                f"{expected} flow client={client_outcome} dest={dest_outcome} never passes "
                "the whole report",
                report.status == "incomplete",
            )


def test_allow_flow_destination_inspection_must_have_found_the_canary() -> None:
    # F5b: an allow flow whose controlled destination WAS inspected but the
    # canary was not found there is vacuous even though enforcement, client
    # and destination logs all say connected.
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    a_enf, a_logs, _ = _passing_allow()
    d_enf, d_c_log, d_d_log = _passing_deny()
    not_found = PlaintextInspection(
        "controlled_destination", "sink-A", "a1", True, decrypted=False, canary_found=False
    )
    evidence = EgressEvidence(
        enforcement=(a_enf, d_enf),
        connection_logs=(*a_logs, d_c_log, d_d_log),
        plaintext_inspections=(not_found, *_clean_mandatory_store_inspections()),
    )
    verdict = next(v for v in verify_egress(plan, evidence).flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-R3-3-F5b",
        "an inspected destination with canary_found=False is vacuous_permitted_flow",
        verdict.status == "failed" and verdict.codes == ("vacuous_permitted_flow",),
    )


def test_allow_flow_capture_canary_is_canary_leaked() -> None:
    # F24: a plaintext packet capture of an allow flow that found the canary
    # is a leak outside the authorized sinks, not a clean pass.
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    base = _clean_all_but_history()
    capture = PacketCaptureObservation("a1", encrypted=False, canary_found=True)
    evidence = EgressEvidence(
        enforcement=base.enforcement,
        connection_logs=base.connection_logs,
        plaintext_inspections=base.plaintext_inspections,
        packet_captures=(capture,),
    )
    verdict = next(v for v in verify_egress(plan, evidence).flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-R3-3-F24",
        "an allow-flow plaintext capture that found the canary is canary_leaked",
        verdict.status == "failed" and verdict.codes == ("canary_leaked",),
    )
    clean_capture = PacketCaptureObservation("a1", encrypted=False, canary_found=False)
    clean = EgressEvidence(
        enforcement=base.enforcement,
        connection_logs=base.connection_logs,
        plaintext_inspections=base.plaintext_inspections,
        packet_captures=(clean_capture,),
    )
    clean_verdict = next(v for v in verify_egress(plan, clean).flow_verdicts if v.flow_id == "a1")
    check(
        "FTF-0906-R3-3-F24",
        "the same capture without the canary leaves the allow flow passed",
        clean_verdict.status == "passed",
    )


def test_policy_digest_must_be_exactly_64_hex() -> None:
    # F25: shape, not just length.
    flows = (_flow("a1", "allow"), _flow("d1", "deny"))
    for bad in ("g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n", " " + "a" * 63, "é" * 64):
        check(
            "FTF-0906-R3-3-F25",
            f"policy_digest {bad!r} is refused",
            _raises(
                InvalidEgressPlanError,
                EgressPlan,
                plan_id="p",
                scope="process",
                policy_digest=bad,
                flows=flows,
            ),
        )
    check(
        "FTF-0906-R3-3-F25",
        "a mixed-case 64-hex digest is accepted",
        EgressPlan(plan_id="p", scope="process", policy_digest="aB" * 32, flows=flows).policy_digest
        == "aB" * 32,
    )


def test_report_hash_binds_every_report_field() -> None:
    # F9 / F10 / F10b / F10c: each field of the report is bound into
    # report_sha256 -- the pure helper is called with ONE field changed.
    from packages.security.egress_capture_verification import _report_sha256

    verdicts = (FlowVerdict("a1", "passed", ()),)
    base_args = ("p", "a" * 64, "process", "passed", verdicts, (), (), ("artifact",))
    base = _report_sha256(*base_args)
    variants = {
        "plan_id": ("q", "a" * 64, "process", "passed", verdicts, (), (), ("artifact",)),
        "policy_digest": ("p", "b" * 64, "process", "passed", verdicts, (), (), ("artifact",)),
        "scope": ("p", "a" * 64, "cluster", "passed", verdicts, (), (), ("artifact",)),
        "status": ("p", "a" * 64, "process", "partial", verdicts, (), (), ("artifact",)),
        "flow_verdicts": (
            "p",
            "a" * 64,
            "process",
            "passed",
            (FlowVerdict("a2", "passed", ()),),
            (),
            (),
            ("artifact",),
        ),
        "flow_verdict_codes": (
            "p",
            "a" * 64,
            "process",
            "passed",
            (FlowVerdict("a1", "passed", ("x",)),),
            (),
            (),
            ("artifact",),
        ),
        "codes": (
            "p",
            "a" * 64,
            "process",
            "passed",
            verdicts,
            ("unplanned_egress_evidence",),
            (),
            ("artifact",),
        ),
        "not_exercised": (
            "p",
            "a" * 64,
            "process",
            "passed",
            verdicts,
            (),
            ("workflow_history",),
            ("artifact",),
        ),
        "inspected_stores": ("p", "a" * 64, "process", "passed", verdicts, (), (), ("error_path",)),
    }
    for field_name, args in variants.items():
        check(
            "FTF-0906-R3-3-HASH",
            f"changing only {field_name} changes report_sha256",
            _report_sha256(*args) != base,
        )
    check("FTF-0906-R3-3-HASH", "the helper is deterministic", _report_sha256(*base_args) == base)

    # End to end through verify_egress: the same pairs of reports differ only
    # in the one field the fault would drop.
    flows = (_flow("a1", "allow"), _flow("d1", "deny"))
    process_plan = _plan(flows)
    cluster_plan = EgressPlan(plan_id="plan-1", scope="cluster", policy_digest=_DIGEST, flows=flows)
    empty = EgressEvidence()
    check(
        "FTF-0906-R3-3-F9",
        "plans that differ only in scope produce different report hashes",
        verify_egress(process_plan, empty).report_sha256
        != verify_egress(cluster_plan, empty).report_sha256,
    )
    only_codes_a = EgressEvidence(
        enforcement=(EnforcementObservation("zz", "process_guard", "deny", "blocked"),),
        plaintext_inspections=(PlaintextInspection("application_log", "l", None, True, False),),
    )
    only_codes_b = EgressEvidence(
        plaintext_inspections=(
            PlaintextInspection("application_log", "l", None, True, False, canary_found=True),
        ),
    )
    report_a = verify_egress(process_plan, only_codes_a)
    report_b = verify_egress(process_plan, only_codes_b)
    check(
        "FTF-0906-R3-3-F10b",
        "fixture isolates codes: same status, verdicts, stores and not_exercised",
        report_a.status == report_b.status == "failed"
        and report_a.flow_verdicts == report_b.flow_verdicts
        and report_a.inspected_stores == report_b.inspected_stores
        and report_a.not_exercised == report_b.not_exercised
        and report_a.codes != report_b.codes,
    )
    check(
        "FTF-0906-R3-3-F10b",
        "reports differing only in codes have different hashes",
        report_a.report_sha256 != report_b.report_sha256,
    )
    no_stores = verify_egress(process_plan, empty)
    one_store = verify_egress(
        process_plan,
        EgressEvidence(
            plaintext_inspections=(PlaintextInspection("artifact", "l", None, True, False),)
        ),
    )
    check(
        "FTF-0906-R3-3-F10",
        "fixture isolates inspected_stores: same status, verdicts, codes",
        no_stores.status == one_store.status
        and no_stores.flow_verdicts == one_store.flow_verdicts
        and no_stores.codes == one_store.codes
        and no_stores.inspected_stores != one_store.inspected_stores,
    )
    check(
        "FTF-0906-R3-3-F10",
        "reports differing only in inspected_stores have different hashes",
        no_stores.report_sha256 != one_store.report_sha256,
    )


def test_report_inspected_stores_excludes_uninspected_records() -> None:
    # F29: the report's own `inspected_stores` lists exactly the stores that
    # were really inspected -- an inspected=False record does not count.
    plan = _plan((_flow("a1", "allow"), _flow("d1", "deny")))
    base = _clean_all_but_history()
    evidence = EgressEvidence(
        enforcement=base.enforcement,
        connection_logs=base.connection_logs,
        plaintext_inspections=(
            *base.plaintext_inspections,
            PlaintextInspection("workflow_history", "wf", None, False, decrypted=False),
        ),
    )
    report = verify_egress(plan, evidence)
    check(
        "FTF-0906-R3-3-F29",
        "report.inspected_stores is exactly the inspected stores, sorted, and omits "
        "the uninspected workflow_history record",
        report.inspected_stores
        == ("application_log", "artifact", "controlled_destination", "error_path"),
    )


def _scan_fragmented(data: bytes, marker, other_markers=()) -> bool:
    """True iff scanning `data` for the marker's `fragmented` placement
    finds it (CanaryLeakedError -- the ledger's own fail-closed path)."""
    ledger = CanaryPlacementLedger({("tool_result", "fragmented")})
    placement = PlacementRecord(marker.canary_id, "tool_result", "fragmented", "r3-test")
    ledger.record_placement(placement)
    markers = {marker.canary_id: marker, **{m.canary_id: m for m in other_markers}}
    try:
        found = scan_store_for_canaries(data, markers, (placement,), ledger)
    except CanaryLeakedError:
        return True
    return found == frozenset({marker.canary_id})


def test_fragmented_canary_is_found_across_separate_places() -> None:
    # FRAGMENT-SCANNER-GAP: pieces that land in separate headers/lines with
    # unrelated bytes between them, in any order, for every split size.
    from packages.security.canary_secret_generator import fragment_canary

    marker = generate_canary()
    other = generate_canary()
    for parts in range(2, 9):
        pieces = fragment_canary(marker, parts)
        headers = "".join(f"X-Canary-Frag-{i}: {piece}\r\n" for i, piece in enumerate(pieces, 1))
        noisy = "Host: 127.0.0.1\r\n" + headers + "Connection: close\r\n"
        check(
            "FTF-0906-R3-5",
            f"{parts} pieces in {parts} separate headers are found",
            _scan_fragmented(noisy.encode(), marker),
        )
        reversed_headers = "".join(f"H{i}: {piece}\r\n" for i, piece in enumerate(pieces[::-1]))
        check(
            "FTF-0906-R3-5",
            f"{parts} pieces in reverse order are found",
            _scan_fragmented(reversed_headers.encode(), marker),
        )
        check(
            "FTF-0906-R3-5",
            f"{parts} pieces in separate log lines of a larger log are found",
            _scan_fragmented(
                ("noise\n" * 50 + "\nline-x ".join(pieces) + "\ntail\n").encode(), marker
            ),
        )
        # Negative, real default code with the attack input PARTLY present:
        # every strict subset of the pieces must NOT count as a leak.
        for missing in range(parts):
            partial = "".join(f"H{i}: {p}\r\n" for i, p in enumerate(pieces) if i != missing)
            check(
                "FTF-0906-R3-5",
                f"{parts} pieces with piece {missing} absent is not found",
                not _scan_fragmented(partial.encode(), marker),
            )
        check(
            "FTF-0906-R3-5",
            f"{parts} pieces of a DIFFERENT canary do not match this one",
            not _scan_fragmented(headers.encode(), other, other_markers=(marker,)),
        )
    scattered = "".join(
        f"H{i}: {piece}\r\n" for i, piece in enumerate(fragment_canary(marker, 2), 1)
    ).encode()
    literal_ledger = CanaryPlacementLedger({("tool_result", "literal")})
    literal_placement = PlacementRecord(marker.canary_id, "tool_result", "literal", "r3-test")
    literal_ledger.record_placement(literal_placement)
    check(
        "FTF-0906-R3-5",
        "pieces are only reassembled for a fragmented placement, never a literal one",
        scan_store_for_canaries(
            scattered, {marker.canary_id: marker}, (literal_placement,), literal_ledger
        )
        == frozenset(),
    )
    check(
        "FTF-0906-R3-5",
        "clean data is not found",
        not _scan_fragmented(b"Host: 127.0.0.1\r\nConnection: close\r\n", marker),
    )
    check(
        "FTF-0906-R3-5",
        "the contiguous literal still counts for a fragmented placement",
        _scan_fragmented(f"x {marker.value} y".encode(), marker),
    )
    first_piece_only = fragment_canary(marker, 2)[0]
    check(
        "FTF-0906-R3-5",
        "a single piece alone (even the longest prefix-bearing one) is not a leak",
        not _scan_fragmented(first_piece_only.encode(), marker),
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
    stores = frozenset({{"controlled_destination", "artifact", "workflow_history",
                          "application_log", "error_path"}})
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
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False, True),
            PlaintextInspection("application_log", "log", None, True, False),
            PlaintextInspection("error_path", "log", None, True, False),
            # "artifact" never inspected.
        ),
    )
    report = verify_egress(plan, evidence)
    return report.status

def history_never_passes():
    stores = frozenset({{"controlled_destination", "workflow_history",
                          "application_log", "error_path", "artifact"}})
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
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False, True),
            PlaintextInspection("application_log", "log", None, True, False),
            PlaintextInspection("error_path", "log", None, True, False),
            PlaintextInspection("artifact", "log", None, True, False),
        ),
    )
    report = verify_egress(plan, evidence)
    return report.status

def unobserved_destination():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
        connection_logs=(ConnectionLogEntry("d1", "client", "refused"),
                          ConnectionLogEntry("d1", "destination", "unobserved")),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "d1")

def narrowed_coverage_rejected():
    try:
        EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                   flows=(flow("a1", "allow"), flow("d1", "deny")),
                   required_stores=frozenset({{"controlled_destination"}}))
        return "constructed"
    except Exception as exc:
        return getattr(exc, "code", "ERROR:" + type(exc).__name__)

def allow_enforcement_mismatch():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("a1", "process_guard", "deny", "blocked"),
                      EnforcementObservation("d1", "process_guard", "deny", "blocked")),
        connection_logs=(
            ConnectionLogEntry("a1", "client", "connected"),
            ConnectionLogEntry("a1", "destination", "connected"),
            ConnectionLogEntry("d1", "client", "refused"),
            ConnectionLogEntry("d1", "destination", "not_seen"),
        ),
        plaintext_inspections=(
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False, True),
        ),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "a1")

def deny_capture_canary_only():
    plan = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                       flows=(flow("a1", "allow"), flow("d1", "deny")))
    evidence = EgressEvidence(
        enforcement=(EnforcementObservation("d1", "process_guard", "deny", "blocked"),),
        connection_logs=(ConnectionLogEntry("d1", "client", "refused"),
                          ConnectionLogEntry("d1", "destination", "not_seen")),
        packet_captures=(PacketCaptureObservation("d1", encrypted=False, canary_found=True),),
    )
    report = verify_egress(plan, evidence)
    return next(v.status for v in report.flow_verdicts if v.flow_id == "d1")

def uninspected_counts_as_covered():
    stores = frozenset({{"controlled_destination", "application_log", "error_path", "artifact"}})
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
            PlaintextInspection("controlled_destination", "sink-A", "a1", True, False, True),
            PlaintextInspection("application_log", "log", None, False, False),
            PlaintextInspection("error_path", "log", None, False, False),
            PlaintextInspection("artifact", "log", None, False, False),
        ),
    )
    report = verify_egress(plan, evidence)
    return report.status

def hash_changes_with_verdict():
    # Two plans whose reports are identical in every OTHER field (status,
    # codes, not_exercised, inspected_stores all come out the same, since
    # neither gets any evidence) -- only each flow_verdicts' own flow_id
    # differs ("a1" vs "x1"). Isolates flow_verdicts as the one input that
    # must still be bound into report_sha256 (F7).
    plan_a = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                         flows=(flow("a1", "allow"), flow("d1", "deny")))
    plan_b = EgressPlan(plan_id="p", scope="process", policy_digest=DIGEST,
                         flows=(flow("x1", "allow"), flow("d1", "deny")))
    report_a = verify_egress(plan_a, EgressEvidence())
    report_b = verify_egress(plan_b, EgressEvidence())
    same_except_verdicts = (
        report_a.status == report_b.status
        and report_a.codes == report_b.codes
        and report_a.not_exercised == report_b.not_exercised
        and report_a.inspected_stores == report_b.inspected_stores
    )
    if not same_except_verdicts:
        return "ERROR:fixture-not-isolated"
    return "different" if report_a.report_sha256 != report_b.report_sha256 else "same"

print(outcome(no_allow_or_deny))
print(outcome(connection_log_missing))
print(outcome(encrypted_only))
print(outcome(deny_leaked_canary))
print(outcome(allow_vacuous))
print(outcome(coverage_missing))
print(outcome(history_never_passes))
print(outcome(unobserved_destination))
print(outcome(narrowed_coverage_rejected))
print(outcome(allow_enforcement_mismatch))
print(outcome(deny_capture_canary_only))
print(outcome(uninspected_counts_as_covered))
print(outcome(hash_changes_with_verdict))
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
    "incomplete",
    "egress_invalid_plan",
    "failed",
    "failed",
    "incomplete",
    "different",
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
        r"elif capture is not None and capture\.encrypted and not has_real_inspection:",
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
        r'if "workflow_history" not in inspected_stores:',
        "if False:",
        6,
    ),
    (
        "UNOBSERVABLE-AS-NOT-SEEN: the unobserved-destination branch removed",
        r'elif dest_log\.outcome == "unobserved" or client_log\.outcome == "unobserved":',
        "elif False:",
        7,
    ),
    (
        "CALLER-NARROWED-COVERAGE: the mandatory-log-store guard removed",
        r"if not self\.required_stores >= _MANDATORY_REQUIRED_STORES:",
        "if False:",
        8,
    ),
    (
        "ALLOW-FLOW-IGNORES-ENFORCEMENT: the enf.decision=='allow' conjunct dropped",
        r'enf\.decision == "allow"\n                and client_log\.outcome == "connected"',
        'True\n                and client_log.outcome == "connected"',
        9,
    ),
    (
        "F3: deny-flow capture canary_found dropped from canary_seen",
        r"canary_seen = any\(insp\.canary_found for insp in flow_inspections\) or \(\n"
        r"                capture is not None and capture\.canary_found\n"
        r"            \)",
        "canary_seen = any(insp.canary_found for insp in flow_inspections)",
        10,
    ),
    (
        "F4: the insp.inspected filter dropped from inspected_stores",
        r"inspected_stores = \{insp\.store for insp in evidence\.plaintext_inspections "
        r"if insp\.inspected\}",
        "inspected_stores = {insp.store for insp in evidence.plaintext_inspections}",
        11,
    ),
    (
        "F7: flow_verdicts dropped from the report_sha256 canonical payload",
        r'            "flow_verdicts": \[\n'
        r"                \{\"flow_id\": v\.flow_id, \"status\": v\.status, "
        r'"codes": list\(v\.codes\)\}\n'
        r"                for v in flow_verdicts\n"
        r"            \],\n",
        "",
        12,
    ),
]


def test_fault_injection_each_rule_is_load_bearing() -> None:
    if os.environ.get("FTF0906_SUITE_FAULT") == "1":
        return  # inside a round-3 fault run: that run must be RED on behaviour alone
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


# Round-3 faults: each is applied to a copy of the real module and this very
# file is re-run as CI runs it (`python3 tests/tasks/test_ftf_0906.py`) in a
# fresh subprocess; the suite must exit non-zero (RED) for each of them.
SUITE_FAULTS = [
    (
        "F-HIST: workflow_history made optional again when required_stores omits it",
        r'if "workflow_history" not in inspected_stores:',
        'if "workflow_history" in plan.required_stores and '
        '"workflow_history" not in inspected_stores:',
    ),
    (
        "F-CLIENT-UNOBS: an unobserved client log no longer makes the flow incomplete",
        r' or client_log\.outcome == "unobserved":',
        ":",
    ),
    (
        "F-CLIENT-UNOBS-CODE: the client_unobserved code dropped",
        r'codes\.append\("client_unobserved"\)',
        "pass",
    ),
    (
        "F5b: dest_inspection.canary_found dropped from `arrived`",
        r"\n                and dest_inspection\.canary_found\n",
        "\n",
    ),
    (
        "F9: scope dropped from report_sha256",
        r'            "scope": scope,\n',
        "",
    ),
    (
        "F10: inspected_stores dropped from report_sha256",
        r'            "inspected_stores": list\(inspected_stores\),\n',
        "",
    ),
    (
        "F10b: codes dropped from report_sha256",
        r'            "codes": list\(codes\),\n',
        "",
    ),
    (
        "F10c: not_exercised dropped from report_sha256",
        r'            "not_exercised": list\(not_exercised\),\n',
        "",
    ),
    (
        "F29: report.inspected_stores counts uninspected stores",
        r"inspected_stores_sorted = tuple\(sorted\(inspected_stores\)\)",
        "inspected_stores_sorted = tuple(\n"
        "    sorted({i.store for i in evidence.plaintext_inspections}))",
    ),
    (
        "F24: an allow-flow capture canary is no longer canary_leaked",
        r"\) or \(capture is not None and capture\.canary_found\)\n"
        r"                if leaked_elsewhere:",
        ")\n                if leaked_elsewhere:",
    ),
    (
        "F25: policy_digest regex accepts any 64 characters",
        r'_HEX64_RE = re\.compile\(r"\[0-9a-fA-F\]\{64\}"\)',
        '_HEX64_RE = re.compile(r".{64}", re.DOTALL)',
    ),
    (
        "F-HASH-PLAN: plan_id dropped from report_sha256",
        r'            "plan_id": plan_id,\n',
        "",
    ),
    (
        "F-HASH-STATUS: status dropped from report_sha256",
        r'            "status": status,\n',
        "",
    ),
    (
        "F-HASH-DIGEST: policy_digest dropped from report_sha256",
        r'            "policy_digest": policy_digest,\n',
        "",
    ),
    (
        "F-FRAG-DROP: the fragmented piece scan removed",
        r'or \(\n            placement\.encoding == "fragmented" and '
        r"_fragments_found\(marker, text\)\n        \)",
        "",
    ),
    (
        "F-FRAG-ANY: any single piece counts as a leak",
        r"if all\(piece in text for piece in fragment_canary\(marker, parts\)\):",
        "if any(piece in text for piece in fragment_canary(marker, parts)):",
    ),
    (
        "F-FRAG-RANGE: only 2-way splits are tried",
        r"tuple\(range\(2, 9\)\)",
        "(2,)",
    ),
    (
        "F-FRAG-SCOPE: pieces scanned for every encoding, not only fragmented",
        r'placement\.encoding == "fragmented" and _fragments_found',
        "_fragments_found",
    ),
]


def test_round3_faults_turn_this_suite_red() -> None:
    if os.environ.get("FTF0906_SUITE_FAULT") == "1":
        return  # already inside a fault run: never recurse
    original = MODULE.read_bytes()
    text = original.decode("utf-8")
    try:
        for description, pattern, replacement in SUITE_FAULTS:
            mutated, count = re.subn(pattern, replacement, text, count=1)
            check("FTF-0906-R3-FAULT", f"fault pattern matches once: {description}", count == 1)
            if count != 1:
                continue
            MODULE.write_text(mutated, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env={**os.environ, "FTF0906_SUITE_FAULT": "1"},
            )
            check(
                "FTF-0906-R3-FAULT",
                f"fault turns the suite RED: {description}",
                result.returncode != 0,
            )
    finally:
        MODULE.write_bytes(original)
    check("FTF-0906-R3-FAULT", "module restored byte-identical", MODULE.read_bytes() == original)


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
