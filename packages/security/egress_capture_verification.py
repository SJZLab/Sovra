"""FTF-0906 -- Egress capture/verification (Section 11-12, 65, 66, 70, 77, 81).

Section 12 is explicit that "no TLS packet capture alone proves absence of
leakage: encrypted traffic can hide content" and requires combining
"allowlist enforcement, verified destination identity, blocked-egress
counters and inspection at authorized application endpoints together."
This module is the pure verifier that combines those evidence kinds into
one report; it never opens a socket itself -- a separate real-integration
test (``tests/integration/test_ftf_0906_egress_verification.py``) is the
one real-socket proof this task's acceptance clause also requires.

Scope boundary (disclosed, matches the QUEUE.md design): this module
verifies *process-scope* egress (one process's own outbound guard) or a
*cluster-scope* rendered policy's digest, by combining the evidence kinds
below. Full cluster-topology evidence (CNI/Calico enforcement, DNS, IPv6,
cloud-metadata paths, live Temporal workflow histories) is FTF-H027's own
job on top of this one; nothing here claims that wider scope.

Canary placements reuse FTF-0905's own ``CanaryMarker`` / ``PlacementRecord``
/ ``CanaryPlacementLedger`` unmodified (Section 0's reuse-first rule). Four
of this task's five named "stores" map onto FTF-0905's own sink
vocabulary; ``controlled_destination`` is deliberately not a 0905 sink at
all, since a canary arriving there on a *permitted* flow is the positive
control the negative_acceptance clause requires, not a leak -- it is
checked by this module's own flow rules below, never fed to the ledger.

Design note on ``PlaintextInspection``/``PacketCaptureObservation`` (a
disclosed refinement of the QUEUE.md sketch, not a silent deviation): each
carries its own ``canary_found: bool``. The ledger itself already fails
closed the moment a real scan finds a canary (``record_checked`` raises
``CanaryLeakedError`` immediately -- the hard, defense-in-depth path
exercised directly by ``scan_store_for_canaries``). ``verify_egress`` needs
the same fact as *data*, not only as an exception a caller might have
already caught several steps earlier, so its own branching (and this
module's own fault table) can be tested as a pure function.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from packages.security.canary_secret_generator import (
    CanaryMarker,
    CanaryPlacementLedger,
    InvalidCanaryRequestError,
    PlacementRecord,
    encode_canary,
)
from packages.security.security_profile_enforcement import SURFACES

__all__ = [
    "STORES",
    "LEDGER_SINK_FOR_STORE",
    "DESTINATION_CLASSES",
    "EXPECTED_DECISIONS",
    "ENFORCEMENT_LAYERS",
    "ENFORCEMENT_DECISIONS",
    "LOG_SIDES",
    "LOG_OUTCOMES",
    "PLAN_SCOPES",
    "REPORT_STATUSES",
    "EgressCaptureVerificationError",
    "InvalidEgressPlanError",
    "FlowClassMissingError",
    "InvalidEgressEvidenceError",
    "PlannedFlow",
    "EgressPlan",
    "EnforcementObservation",
    "ConnectionLogEntry",
    "PlaintextInspection",
    "PacketCaptureObservation",
    "EgressEvidence",
    "FlowVerdict",
    "EgressVerificationReport",
    "scan_store_for_canaries",
    "verify_egress",
]

# Section 12's own five evidence "stores" for this task.
STORES: tuple[str, ...] = (
    "controlled_destination",
    "application_log",
    "error_path",
    "artifact",
    "workflow_history",
)
LEDGER_SINK_FOR_STORE: dict[str, str] = {
    "application_log": "trace",
    "error_path": "error_path",
    "artifact": "artifact",
    "workflow_history": "pipeline_history",
}
DESTINATION_CLASSES: tuple[str, ...] = ("internal", "external", "metadata")
EXPECTED_DECISIONS: tuple[str, ...] = ("allow", "deny")
ENFORCEMENT_LAYERS: tuple[str, ...] = ("process_guard", "cni")
ENFORCEMENT_DECISIONS: tuple[str, ...] = ("allow", "deny")
LOG_SIDES: tuple[str, ...] = ("client", "destination")
# "unobserved" (Law 17): the real state of a side was never actually
# checked -- never fabricated as "not_seen" (which asserts a real,
# checked absence). A caller that cannot observe a destination (e.g. a
# cloud-metadata address this sandbox cannot bind a listener on) must say
# so, not report a convenient default.
LOG_OUTCOMES: tuple[str, ...] = (
    "connected",
    "refused",
    "timeout",
    "reset",
    "not_seen",
    "unobserved",
)
PLAN_SCOPES: tuple[str, ...] = ("process", "cluster")
REPORT_STATUSES: tuple[str, ...] = ("passed", "failed", "incomplete", "partial")

_STRING_MAX_LENGTH = 200
_MAX_FLOWS = 200
_HEX64_RE = re.compile(r"[0-9a-fA-F]{64}")
# Section 12's own log/error-path/artifact stores are never optional: a
# caller cannot narrow `required_stores` below them and still claim full
# coverage (CALLER-NARROWED-COVERAGE). `workflow_history` stays separately
# handled (always forces `partial`, never `passed`, independent of
# `required_stores`) and `controlled_destination`'s own coverage is
# already forced per-flow by the allow branch's positive-control check.
_MANDATORY_REQUIRED_STORES: frozenset[str] = frozenset(
    {"application_log", "error_path", "artifact"}
)
_PERCENT_HEX_RE = re.compile(r"%[0-9a-fA-F]{2}")


class EgressCaptureVerificationError(Exception):
    """Base; every subclass has a stable `code` for the API error body."""

    code = "egress_capture_verification_error"


class InvalidEgressPlanError(EgressCaptureVerificationError):
    code = "egress_invalid_plan"


class FlowClassMissingError(InvalidEgressPlanError):
    """negative_acceptance's own split: a plan that only ever tests allow
    (or only ever tests deny) flows cannot prove enforcement at all."""

    code = "flow_class_missing"


class InvalidEgressEvidenceError(EgressCaptureVerificationError):
    code = "egress_invalid_evidence"


def _present(value: object) -> bool:
    return type(value) is str and bool(value) and not value.isspace()


def _text(value: object, what: str) -> str:
    if not _present(value) or len(value) > _STRING_MAX_LENGTH:  # type: ignore[arg-type]
        raise InvalidEgressPlanError(f"{what} must be a non-empty bounded string")
    return value  # type: ignore[return-value]


def _member(value: object, allowed: tuple[str, ...], what: str) -> str:
    # Exact-type check before membership: an unhashable value must raise our
    # own error, not a bare TypeError from `in`.
    if type(value) is not str or value not in allowed:
        raise InvalidEgressPlanError(f"{what} must be one of {allowed}")
    return value


def _bool(value: object, what: str) -> bool:
    if type(value) is not bool:
        raise InvalidEgressPlanError(f"{what} must be a bool")
    return value


def _hex64(value: object, what: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None:
        raise InvalidEgressPlanError(f"{what} must be a 64-character hex digest")
    return value


@dataclass(frozen=True)
class PlannedFlow:
    """One flow this plan exercises: an expected enforcement outcome plus
    the canary placements authorized for it (FTF-0905 `PlacementRecord`s,
    targeting FTF-0905's own sink vocabulary -- never `controlled_destination`,
    which is not a 0905 sink)."""

    flow_id: str
    surface: str
    destination_label: str
    destination_class: str
    expected: str
    canary_placements: tuple[PlacementRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _text(self.flow_id, "flow_id")
        _member(self.surface, SURFACES, "surface")
        _text(self.destination_label, "destination_label")
        _member(self.destination_class, DESTINATION_CLASSES, "destination_class")
        _member(self.expected, EXPECTED_DECISIONS, "expected")
        if not isinstance(self.canary_placements, tuple):
            raise InvalidEgressPlanError("canary_placements must be a tuple")
        for placement in self.canary_placements:
            if not isinstance(placement, PlacementRecord):
                raise InvalidEgressPlanError("every canary placement must be a PlacementRecord")


@dataclass(frozen=True)
class EgressPlan:
    """The declared test plan. Refuses (`FlowClassMissingError`) a plan with
    no `allow` flow or no `deny` flow -- proving enforcement needs both a
    flow that must get through and one that must not."""

    plan_id: str
    scope: str
    policy_digest: str
    flows: tuple[PlannedFlow, ...]
    required_stores: frozenset[str] = field(default_factory=lambda: frozenset(STORES))

    def __post_init__(self) -> None:
        _text(self.plan_id, "plan_id")
        _member(self.scope, PLAN_SCOPES, "scope")
        _hex64(self.policy_digest, "policy_digest")
        if not isinstance(self.flows, tuple) or not self.flows:
            raise InvalidEgressPlanError("flows must be a non-empty tuple")
        if len(self.flows) > _MAX_FLOWS:
            raise InvalidEgressPlanError(f"flows must not exceed {_MAX_FLOWS}")
        seen: set[str] = set()
        for flow in self.flows:
            if not isinstance(flow, PlannedFlow):
                raise InvalidEgressPlanError("every flow must be a PlannedFlow")
            if flow.flow_id in seen:
                raise InvalidEgressPlanError(f"duplicate flow_id {flow.flow_id!r}")
            seen.add(flow.flow_id)
        if not isinstance(self.required_stores, frozenset) or not self.required_stores:
            raise InvalidEgressPlanError("required_stores must be a non-empty frozenset")
        for store in self.required_stores:
            _member(store, STORES, "required store")
        if not self.required_stores >= _MANDATORY_REQUIRED_STORES:
            missing = sorted(_MANDATORY_REQUIRED_STORES - self.required_stores)
            raise InvalidEgressPlanError(
                f"required_stores cannot omit the mandatory log stores {missing}"
            )
        expected_decisions = {flow.expected for flow in self.flows}
        if "allow" not in expected_decisions or "deny" not in expected_decisions:
            raise FlowClassMissingError(
                "a plan must exercise at least one allow flow and one deny flow"
            )


@dataclass(frozen=True)
class EnforcementObservation:
    flow_id: str
    layer: str
    decision: str
    code: str

    def __post_init__(self) -> None:
        _text(self.flow_id, "flow_id")
        _member(self.layer, ENFORCEMENT_LAYERS, "layer")
        _member(self.decision, ENFORCEMENT_DECISIONS, "decision")
        _text(self.code, "code")


@dataclass(frozen=True)
class ConnectionLogEntry:
    flow_id: str
    side: str
    outcome: str

    def __post_init__(self) -> None:
        _text(self.flow_id, "flow_id")
        _member(self.side, LOG_SIDES, "side")
        _member(self.outcome, LOG_OUTCOMES, "outcome")


@dataclass(frozen=True)
class PlaintextInspection:
    """`inspected` records that this store was actually looked at for this
    evidence set (coverage); `decrypted` records whether seeing its content
    required decryption (Section 12's own TLS-capture-is-insufficient
    point); `canary_found` is this check's own result, set by the caller
    from `scan_store_for_canaries`'s return value (or from the
    `CanaryLeakedError` it raises -- see the module docstring)."""

    store: str
    store_id: str
    flow_id: str | None
    inspected: bool
    decrypted: bool
    canary_found: bool = False

    def __post_init__(self) -> None:
        _member(self.store, STORES, "store")
        _text(self.store_id, "store_id")
        if self.flow_id is not None:
            _text(self.flow_id, "flow_id")
        _bool(self.inspected, "inspected")
        _bool(self.decrypted, "decrypted")
        _bool(self.canary_found, "canary_found")
        if self.canary_found and not self.inspected:
            raise InvalidEgressEvidenceError("a canary cannot be found at a store never inspected")


@dataclass(frozen=True)
class PacketCaptureObservation:
    flow_id: str
    encrypted: bool
    canary_found: bool

    def __post_init__(self) -> None:
        _text(self.flow_id, "flow_id")
        _bool(self.encrypted, "encrypted")
        _bool(self.canary_found, "canary_found")


@dataclass(frozen=True)
class EgressEvidence:
    enforcement: tuple[EnforcementObservation, ...] = field(default_factory=tuple)
    connection_logs: tuple[ConnectionLogEntry, ...] = field(default_factory=tuple)
    plaintext_inspections: tuple[PlaintextInspection, ...] = field(default_factory=tuple)
    packet_captures: tuple[PacketCaptureObservation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name, items, cls in (
            ("enforcement", self.enforcement, EnforcementObservation),
            ("connection_logs", self.connection_logs, ConnectionLogEntry),
            ("plaintext_inspections", self.plaintext_inspections, PlaintextInspection),
            ("packet_captures", self.packet_captures, PacketCaptureObservation),
        ):
            if not isinstance(items, tuple):
                raise InvalidEgressEvidenceError(f"{name} must be a tuple")
            for item in items:
                if not isinstance(item, cls):
                    raise InvalidEgressEvidenceError(f"every {name} item must be a {cls.__name__}")


@dataclass(frozen=True)
class FlowVerdict:
    flow_id: str
    status: str
    codes: tuple[str, ...]


@dataclass(frozen=True)
class EgressVerificationReport:
    status: str
    plan_id: str
    # REPORT-UNBOUND: the plan this report actually verified -- bound into
    # `report_sha256` below, so a report can never be silently re-attached
    # to a different policy/scope/coverage after the fact.
    policy_digest: str
    scope: str
    flow_verdicts: tuple[FlowVerdict, ...]
    codes: tuple[str, ...]
    not_exercised: tuple[str, ...]
    inspected_stores: tuple[str, ...]
    report_sha256: str


def _needles_for_encoding(marker: CanaryMarker, encoding: str) -> tuple[str, ...]:
    """The exact bytes a real log/header could carry for one declared
    encoding -- never just the one canonical form `encode_canary` returns.
    `hex` is case-insensitive in every real consumer (`bytes.hex()` is
    always lowercase, but a log line, header dump or upstream proxy may
    upper-case it); `%`-encoding is case-insensitive in its own two hex
    digits per RFC 3986. Missing either variant is a real scanner gap
    (found verifying this task's own real integration fixture), not a
    theoretical one."""
    if encoding in ("literal", "fragmented"):
        return (marker.value,)
    base = encode_canary(marker, encoding)
    if encoding == "hex":
        return (base, base.upper())
    if encoding == "url":
        # `urllib.parse.quote` always emits upper-case percent-escapes, but
        # a real intermediary (reverse proxy, log shipper) may re-emit
        # them lower-case -- the variant actually missing, not an
        # already-covered uppercase no-op. Always include both: when there
        # is no percent-escape to re-case, `lower_percent` just repeats
        # `base`, a harmless duplicate substring check.
        lower_percent = _PERCENT_HEX_RE.sub(lambda m: m.group(0).lower(), base)
        return (base, lower_percent)
    return (base,)


def scan_store_for_canaries(
    data: bytes,
    markers: Mapping[str, CanaryMarker],
    placements: Collection[PlacementRecord],
    ledger: CanaryPlacementLedger,
) -> frozenset[str]:
    """Scan `data` for every (canary_id, sink, encoding) combination named
    in `placements`, recording each individual check in `ledger` (which
    itself raises `CanaryLeakedError` immediately the moment any one check
    is a genuine find -- see `CanaryPlacementLedger.record_checked`; this
    function never catches that, by design). `fragmented` is scanned as the
    marker's own literal value: `fragment_canary`'s pieces are contiguous
    and reassemble to it exactly, so a caller that deliberately placed
    fragments non-contiguously across two real sink fields (e.g. two HTTP
    headers) must itself concatenate the candidate text before calling this
    function -- the exact same text a human or a downstream log scraper
    would actually see.

    Returns the set of canary_ids found, for a caller building a
    `PlaintextInspection`/`PacketCaptureObservation`'s own `canary_found`
    field in the normal (non-raising, nothing found) case.
    """
    if not isinstance(data, bytes):
        raise InvalidEgressEvidenceError("data must be bytes")
    if not isinstance(ledger, CanaryPlacementLedger):
        raise InvalidEgressEvidenceError("ledger must be a CanaryPlacementLedger")
    text = data.decode("utf-8", errors="replace")
    found: set[str] = set()
    for placement in placements:
        if not isinstance(placement, PlacementRecord):
            raise InvalidEgressEvidenceError("every placement must be a PlacementRecord")
        marker = markers.get(placement.canary_id)
        if marker is None or not isinstance(marker, CanaryMarker):
            raise InvalidEgressEvidenceError(
                f"placement names unknown canary id {placement.canary_id!r}"
            )
        try:
            needles = _needles_for_encoding(marker, placement.encoding)
        except InvalidCanaryRequestError as exc:
            # Re-raise as this module's own public exception: a caller
            # catching `InvalidEgressEvidenceError` (this function's own
            # documented error type) must not miss a bad-encoding
            # rejection just because it happened to be routed through
            # FTF-0905's own `encode_canary` instead of one of this
            # function's own checks above.
            raise InvalidEgressEvidenceError(str(exc)) from exc
        is_found = any(needle in text for needle in needles)
        ledger.record_checked(
            placement.canary_id, placement.sink, placement.encoding, found=is_found
        )
        if is_found:
            found.add(placement.canary_id)
    return frozenset(found)


def _report_sha256(
    plan_id: str,
    policy_digest: str,
    scope: str,
    status: str,
    flow_verdicts: tuple[FlowVerdict, ...],
    codes: tuple[str, ...],
    not_exercised: tuple[str, ...],
    inspected_stores: tuple[str, ...],
) -> str:
    # Content-free by construction: every input here is an id, a closed-set
    # status/code string or a count -- never a canary value, host or body.
    # Every field the report itself carries is bound in here (REPORT-UNBOUND):
    # dropping any one of them (flow_verdicts included -- fault F7) must
    # change the digest for the same report.
    canonical = json.dumps(
        {
            "plan_id": plan_id,
            "policy_digest": policy_digest,
            "scope": scope,
            "status": status,
            "flow_verdicts": [
                {"flow_id": v.flow_id, "status": v.status, "codes": list(v.codes)}
                for v in flow_verdicts
            ],
            "codes": list(codes),
            "not_exercised": list(not_exercised),
            "inspected_stores": list(inspected_stores),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aggregate_overall_status(
    flow_verdicts: tuple[FlowVerdict, ...],
    missing_required: Collection[str],
    not_exercised: tuple[str, ...],
    *,
    unattributed_leak: bool = False,
    unplanned_evidence: bool = False,
) -> str:
    """Exact-match aggregation, split out of `verify_egress` so it is
    directly testable: `FlowVerdict.status` carries no dataclass-level
    validation (it is always one of this module's own lowercase literals
    in real use), so a malformed value must never be silently folded into
    `failed`/`incomplete` by case or whitespace normalization.

    `unattributed_leak`/`unplanned_evidence` always win (UNATTRIBUTED-LEAK-
    IGNORED / UNPLANNED-EGRESS-IGNORED): a canary found at a store never
    tied to any flow, or evidence naming a flow_id this plan never
    declared, is itself proof something happened outside what was tested
    -- the report can never say `passed` (or merely `incomplete`) through
    a per-flow rollup that was never built to see it."""
    if unattributed_leak or unplanned_evidence:
        return "failed"
    if any(v.status == "failed" for v in flow_verdicts):
        return "failed"
    if missing_required or any(v.status == "incomplete" for v in flow_verdicts):
        return "incomplete"
    if not_exercised:
        return "partial"
    return "passed"


def verify_egress(plan: EgressPlan, evidence: EgressEvidence) -> EgressVerificationReport:
    """Combine enforcement, connection logs and plaintext/packet-capture
    inspection into one report. Never claims `passed` when `workflow_history`
    was never exercised (always `partial` with `not_exercised` naming it,
    per this task's own disclosed scope -- no Temporal workflow exists
    here), and never lets an encrypted-only packet capture stand in for a
    real plaintext inspection (Section 12's own negative clause).

    Every piece of evidence is read, never silently dropped: evidence
    naming a flow_id this plan never declared is UNPLANNED-EGRESS, and a
    canary found at a store with no flow attribution at all (`flow_id is
    None`) is UNATTRIBUTED-LEAK -- both force the whole report `failed`
    regardless of how every declared flow's own verdict came out."""
    if not isinstance(plan, EgressPlan):
        raise InvalidEgressPlanError("plan must be an EgressPlan")
    if not isinstance(evidence, EgressEvidence):
        raise InvalidEgressEvidenceError("evidence must be an EgressEvidence")

    plan_flow_ids = {flow.flow_id for flow in plan.flows}

    enforcement_by_flow: dict[str, EnforcementObservation] = {}
    for obs in evidence.enforcement:
        if obs.flow_id in enforcement_by_flow:
            raise InvalidEgressEvidenceError(
                f"duplicate enforcement observation for flow {obs.flow_id!r}"
            )
        enforcement_by_flow[obs.flow_id] = obs

    logs_by_flow: dict[str, dict[str, ConnectionLogEntry]] = {}
    for entry in evidence.connection_logs:
        side_map = logs_by_flow.setdefault(entry.flow_id, {})
        if entry.side in side_map:
            raise InvalidEgressEvidenceError(
                f"duplicate {entry.side} connection log for flow {entry.flow_id!r}"
            )
        side_map[entry.side] = entry

    captures_by_flow: dict[str, PacketCaptureObservation] = {}
    for capture_obs in evidence.packet_captures:
        if capture_obs.flow_id in captures_by_flow:
            raise InvalidEgressEvidenceError(
                f"duplicate packet capture for flow {capture_obs.flow_id!r}"
            )
        captures_by_flow[capture_obs.flow_id] = capture_obs

    # UNPLANNED-EGRESS-IGNORED: evidence for a flow_id this plan never
    # declared is itself proof of egress outside the tested plan -- never
    # silently dropped just because the per-flow loop below only walks
    # `plan.flows`.
    unplanned_evidence = (
        any(obs.flow_id not in plan_flow_ids for obs in evidence.enforcement)
        or any(entry.flow_id not in plan_flow_ids for entry in evidence.connection_logs)
        or any(cap.flow_id not in plan_flow_ids for cap in evidence.packet_captures)
        or any(
            insp.flow_id is not None and insp.flow_id not in plan_flow_ids
            for insp in evidence.plaintext_inspections
        )
    )

    # UNATTRIBUTED-LEAK-IGNORED: a canary found at a store never tied to
    # any one flow (`flow_id is None` -- a whole-store inspection, e.g. a
    # shared access log or the workflow history) is a real leak with
    # nowhere to attribute it, not evidence that happens to fall outside
    # every flow's own `flow_inspections` filter.
    unattributed_leak = any(
        insp.canary_found and insp.flow_id is None for insp in evidence.plaintext_inspections
    )

    inspected_stores = {insp.store for insp in evidence.plaintext_inspections if insp.inspected}

    flow_verdicts: list[FlowVerdict] = []
    for flow in plan.flows:
        codes: list[str] = []
        enf = enforcement_by_flow.get(flow.flow_id)
        sides = logs_by_flow.get(flow.flow_id, {})
        client_log = sides.get("client")
        dest_log = sides.get("destination")
        flow_inspections = tuple(
            insp for insp in evidence.plaintext_inspections if insp.flow_id == flow.flow_id
        )
        capture = captures_by_flow.get(flow.flow_id)
        # ENCRYPTED-CAPTURE-EVADABLE: a `PlaintextInspection` record that
        # was never actually inspected (`inspected=False`) must never
        # count as "something to corroborate" an encrypted capture with --
        # only a real, inspected plaintext record for this same flow can.
        has_real_inspection = any(insp.inspected for insp in flow_inspections)

        if enf is None or client_log is None or dest_log is None:
            status = "incomplete"
            codes.append("connection_log_missing")
        elif dest_log.outcome == "unobserved":
            # UNOBSERVABLE-AS-NOT-SEEN (Law 17): an unchecked destination
            # state is never treated as a confirmed "nothing arrived" --
            # that would let a flow this test genuinely could not observe
            # pass a deny flow, or fail an allow flow, on a claim nobody
            # actually verified.
            status = "incomplete"
            codes.append("destination_unobserved")
        elif capture is not None and capture.encrypted and not has_real_inspection:
            # Section 12: "No TLS packet capture alone proves absence of
            # leakage" -- a capture with nothing else to corroborate it can
            # never stand in for a real inspection.
            status = "incomplete"
            codes.append("encrypted_capture_insufficient")
        elif flow.expected == "deny":
            canary_seen = any(insp.canary_found for insp in flow_inspections) or (
                capture is not None and capture.canary_found
            )
            if (
                enf.decision == "deny"
                and client_log.outcome != "connected"
                and dest_log.outcome == "not_seen"
                and not canary_seen
            ):
                status = "passed"
            else:
                status = "failed"
                codes.append("unauthorized_transfer")
        else:  # expected == "allow"
            # VACUOUS-POSITIVE-CONTROL / ALLOW-FLOW-IGNORES-ENFORCEMENT: a
            # permitted flow is only proven to have really gone through
            # when enforcement, the client side and the destination side
            # all agree it connected, AND the controlled destination's own
            # inspection actually recovered the canary there (the positive
            # control Section 12 requires) -- never merely a record that
            # some inspection "happened" with no canary ever confirmed.
            dest_inspection = next(
                (
                    insp
                    for insp in flow_inspections
                    if insp.store == "controlled_destination" and insp.inspected
                ),
                None,
            )
            arrived = (
                enf.decision == "allow"
                and client_log.outcome == "connected"
                and dest_log.outcome == "connected"
                and dest_inspection is not None
                and dest_inspection.canary_found
            )
            if not arrived:
                status = "failed"
                codes.append("vacuous_permitted_flow")
            else:
                authorized_sinks = {p.sink for p in flow.canary_placements}
                leaked_elsewhere = any(
                    insp.canary_found
                    for insp in flow_inspections
                    if insp.store != "controlled_destination"
                    and LEDGER_SINK_FOR_STORE.get(insp.store) not in authorized_sinks
                ) or (capture is not None and capture.canary_found)
                if leaked_elsewhere:
                    status = "failed"
                    codes.append("canary_leaked")
                else:
                    status = "passed"

        flow_verdicts.append(FlowVerdict(flow_id=flow.flow_id, status=status, codes=tuple(codes)))

    report_codes: list[str] = []
    missing_required = plan.required_stores - inspected_stores - {"workflow_history"}
    if missing_required:
        report_codes.append("inspection_coverage_missing")
    if unplanned_evidence:
        report_codes.append("unplanned_egress_evidence")
    if unattributed_leak:
        report_codes.append("unattributed_canary_found")

    not_exercised: tuple[str, ...] = ()
    if "workflow_history" in plan.required_stores and "workflow_history" not in inspected_stores:
        not_exercised = ("workflow_history",)

    overall = _aggregate_overall_status(
        tuple(flow_verdicts),
        missing_required,
        not_exercised,
        unattributed_leak=unattributed_leak,
        unplanned_evidence=unplanned_evidence,
    )
    inspected_stores_sorted = tuple(sorted(inspected_stores))

    report_sha256 = _report_sha256(
        plan.plan_id,
        plan.policy_digest,
        plan.scope,
        overall,
        tuple(flow_verdicts),
        tuple(report_codes),
        not_exercised,
        inspected_stores_sorted,
    )
    return EgressVerificationReport(
        status=overall,
        plan_id=plan.plan_id,
        policy_digest=plan.policy_digest,
        scope=plan.scope,
        flow_verdicts=tuple(flow_verdicts),
        codes=tuple(report_codes),
        not_exercised=not_exercised,
        inspected_stores=inspected_stores_sorted,
        report_sha256=report_sha256,
    )
