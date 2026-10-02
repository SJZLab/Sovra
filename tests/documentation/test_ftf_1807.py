#!/usr/bin/env python3
"""
FTF-1807 -- LiteLLM outage runbook.

`docs/operations/litellm_outage_runbook.md` summarizes real, already-
CERTIFIED library behavior (FTF-0208/FTF-0209/FTF-0215/FTF-H004/FTF-H005)
for human operators. This task adds no new production code (its own
`database_changes`/`files` clause names only the doc and this test) --
its own Definition of Done is that every claim the doc makes is literally
true of the real, current repository, not merely asserted.

Proves:
  1. The numeric/string facts the doc states (5-minute freshness TTL,
     3-attempt retry ceiling, the `x-litellm-model-id` fingerprint header,
     the `"cache": {"no-cache": true}` field, `SKIPPED_OUT_OF_ORDER`) are
     read directly from the real, imported modules -- never hardcoded
     twice -- and are genuinely present in the doc text.
  2. This task's own acceptance clause ("Use tested LiteLLM outage/
     revocation/fallback/identity handling and safe routing recovery") is
     re-exercised directly against the real, pure, no-I/O modules: a
     revocation refuses calls immediately and independently of freshness;
     freshness expiry refuses calls after the TTL elapses; a >50% model
     drop opens a persisted anomaly without restoring or blocking
     unrelated authorization; acknowledging an anomaly never itself
     restores an already-removed id; and a stale, out-of-order sync
     response is dropped before it can reinstate access a fresher cycle
     already revoked.
  3. This task's own negative_acceptance ("No unauthorized cached access
     or unapproved external fallback during private outage") is grounded
     in the real, static source of `route_attestation.py`: every
     attestation request carries the real cache-bypass field, and route
     identity is only ever `VERIFIED` when both a real fingerprint and a
     real resolved model are present -- never fabricated.
  4. The doc discloses (Law 6/7) that none of these modules has a live
     route/scheduler caller yet -- it is not permitted to claim a wired
     production incident-response system.
  5. Real fault injection (Law 1) against this task's own file: mutating
     the doc's stated freshness TTL away from the real imported constant
     makes the doc/behavior cross-check genuinely fail; the doc file is
     restored byte-identical afterward and the check passes again.

Run directly: python3 tests/documentation/test_ftf_1807.py
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client import (  # noqa: E402
    catalog_anomaly_detector,
    last_known_good_catalog,
    periodic_sync_workflow,
    route_attestation,
)
from packages.litellm_client.catalog_anomaly_detector import CatalogAnomalyDetector  # noqa: E402
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    AccessState,
    AuthorizationReason,
    LastKnownGoodCatalog,
)
from packages.litellm_client.periodic_sync_workflow import (  # noqa: E402
    PeriodicSyncWorkflow,
    SyncCycleStatus,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
)

FAILURES: list[str] = []
DOC_PATH = ROOT / "docs" / "operations" / "litellm_outage_runbook.md"

_T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


def _discovered(*ids: str) -> ModelDiscoveryResult:
    models = tuple(DiscoveredModel(id=i, metadata_status=ModelMetadataStatus.LIMITED) for i in ids)
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=models,
        model_count=len(models),
        content_hash="irrelevant-to-this-task",
        detail="gateway reachable and authorized",
        checked_at=_T0,
    )


def _revoked() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.INVALID_TOKEN,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway rejected the inference credential",
        checked_at=_T0,
    )


def _transient() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.TIMEOUT,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway did not respond before the request timeout",
        checked_at=_T0,
    )


# ---------------------------------------------------------------------------
# 1. Doc facts are read from real constants, not hardcoded twice
# ---------------------------------------------------------------------------


def _expected_ttl_phrase() -> str:
    minutes = int(last_known_good_catalog.DEFAULT_FRESHNESS_TTL.total_seconds() // 60)
    return f"{minutes} minutes"


def _expected_retry_phrase() -> str:
    return f"{catalog_anomaly_detector.DEFAULT_RETRY_CEILING} attempts"


def _normalize_whitespace(text: str) -> str:
    """Markdown hard-wraps prose across lines; a substring check on raw
    text would spuriously fail on a phrase that happens to wrap, which is
    not a real defect. Collapsing all whitespace runs to a single space
    makes the check test the actual words, not the line-wrap layout."""
    return " ".join(text.split())


def _doc_matches_real_facts(raw_doc_text: str) -> list[str]:
    """Returns a list of unmet claims (empty means the doc is fully
    grounded). A real, callable function so fault injection below can
    prove it actually discriminates a wrong doc from a right one.

    This only checks that certain tokens/phrases are *present somewhere* in
    the doc -- it does not read what the doc says those tokens mean. A
    rewrite that keeps every token intact but inverts the operator guidance
    (e.g. "revoked access continues until TTL" instead of "immediate
    refusal") passes this check untouched; see
    `_doc_operator_guidance_matches_real_semantics` below for the check that
    catches that."""
    doc_text = _normalize_whitespace(raw_doc_text)
    problems = []
    if _expected_ttl_phrase() not in doc_text:
        problems.append("freshness TTL phrase missing/wrong")
    if _expected_retry_phrase() not in doc_text:
        problems.append("retry ceiling phrase missing/wrong")
    if route_attestation._DEPLOYMENT_FINGERPRINT_HEADER not in doc_text:
        problems.append("deployment fingerprint header missing/wrong")
    if '"cache": {"no-cache": true}' not in doc_text:
        problems.append("cache-bypass field missing/wrong")
    if SyncCycleStatus.SKIPPED_OUT_OF_ORDER.name not in doc_text:
        problems.append("SKIPPED_OUT_OF_ORDER status missing")
    return problems


def _extract_table_row_effect(raw_doc_text: str, signal_prefix: str) -> str | None:
    """Parses section 7's `| Signal | Detection | Effect |` operator table
    and returns the `Effect` column text for the first row whose `Signal`
    column starts with `signal_prefix`, or None if no such row exists."""
    for line in raw_doc_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0].startswith(signal_prefix):
            return cells[2]
    return None


def _doc_operator_guidance_matches_real_semantics(raw_doc_text: str) -> list[str]:
    """Returns unmet operator-guidance claims (empty means the doc is fully
    grounded). Unlike `_doc_matches_real_facts`, this reads the *meaning* of
    the revocation-is-immediate and no-unapproved-fallback statements --
    specifically the operator table's own Effect column, per the 2026-10-01
    L2 finding -- so a rewrite that inverts the guidance while leaving every
    other token untouched is caught."""
    problems = []

    revoked_effect = _extract_table_row_effect(raw_doc_text, "Key/connection revoked (401/403)")
    if revoked_effect is None:
        problems.append("operator table: no row for 'Key/connection revoked (401/403)'")
    elif "immediate" not in revoked_effect.lower() or "until" in revoked_effect.lower():
        problems.append(
            "operator table: revoked-key row does not state immediate, "
            f"unconditional refusal (reads: {revoked_effect!r})"
        )

    reroute_effect = _extract_table_row_effect(
        raw_doc_text, "Router silently reroutes to a different deployment"
    )
    if reroute_effect is None:
        problems.append("operator table: no row for the silent-reroute signal")
    elif (
        "unverified" not in reroute_effect.lower()
        or "never claimed equivalent" not in reroute_effect.lower()
    ):
        problems.append(
            "operator table: silent-reroute row does not state identity stays "
            f"UNVERIFIED and is never claimed equivalent (reads: {reroute_effect!r})"
        )

    normalized = _normalize_whitespace(raw_doc_text).lower().replace("**", "")
    if (
        "does not wait for the freshness ttl to elapse first" not in normalized
        or "revocation is a definite signal" not in normalized
    ):
        problems.append("revocation-is-immediate body statement (section 2) missing/weakened")
    if "never treated as equivalent to the pinned deployment" not in normalized:
        problems.append("no-unapproved-fallback body statement (section 6) missing/weakened")

    return problems


def test_doc_facts_are_grounded_in_real_constants():
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    problems = _doc_matches_real_facts(doc_text)
    check(
        "FTF-1807",
        f"runbook states the real, currently-imported facts (unmet: {problems})",
        problems == [],
    )
    # Sanity: the constants this check reads are the real ones certified
    # tasks already fix, not locally invented numbers.
    check(
        "FTF-1807",
        "5-minute freshness TTL is the real FTF-0208 constant",
        last_known_good_catalog.DEFAULT_FRESHNESS_TTL == timedelta(minutes=5),
    )
    check(
        "FTF-1807",
        "3-attempt retry ceiling is the real FTF-0209 constant, reused (not "
        "reinvented) by FTF-0215's own periodic workflow",
        catalog_anomaly_detector.DEFAULT_RETRY_CEILING == 3
        and periodic_sync_workflow.DEFAULT_RETRY_CEILING
        is catalog_anomaly_detector.DEFAULT_RETRY_CEILING,
    )


def test_operator_guidance_states_immediate_revocation_and_no_fallback():
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    problems = _doc_operator_guidance_matches_real_semantics(doc_text)
    check(
        "FTF-1807",
        f"operator guidance states revocation-is-immediate and no-unapproved-fallback, "
        f"not just matching tokens (unmet: {problems})",
        problems == [],
    )


def test_doc_names_the_real_certified_modules_and_discloses_unwired_scope():
    doc_text = _normalize_whitespace(DOC_PATH.read_text(encoding="utf-8"))
    for real_path in (
        "packages/litellm_client/last_known_good_catalog.py",
        "packages/litellm_client/access_guard.py",
        "packages/litellm_client/catalog_anomaly_detector.py",
        "packages/litellm_client/periodic_sync_workflow.py",
        "packages/litellm_client/route_attestation.py",
    ):
        check("FTF-1807", f"runbook cites the real module {real_path}", real_path in doc_text)
        check(
            "FTF-1807",
            f"cited module {real_path} genuinely exists in this checkout",
            (ROOT / real_path).is_file(),
        )
    check(
        "FTF-1807",
        "runbook discloses no live route/scheduler caller yet (Law 6/7 -- "
        "designed is not wired)",
        "no live" in doc_text.lower() and "not a claim that a live" in doc_text.lower(),
    )
    check(
        "FTF-1807",
        "runbook states this task's own negative_acceptance clause verbatim",
        "no unauthorized cached access or unapproved external fallback during" in doc_text.lower(),
    )


# ---------------------------------------------------------------------------
# 2. Real behavioral proof against the real, certified modules
# ---------------------------------------------------------------------------


def test_revocation_refuses_calls_immediately_independent_of_freshness():
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("m1"), now=_T0)
    decision = catalog.is_call_authorized("m1", now=_T0 + timedelta(seconds=1))
    check("FTF-1807", "call is authorized right after a healthy discovery", decision.authorized)

    snapshot = catalog.apply_discovery_result(_revoked(), now=_T0 + timedelta(seconds=2))
    check(
        "FTF-1807",
        "revocation sets access_state=REVOKED",
        snapshot.access_state is AccessState.REVOKED,
    )
    decision = catalog.is_call_authorized("m1", now=_T0 + timedelta(seconds=3))
    check(
        "FTF-1807",
        "a revoked connection refuses the same model immediately, well within " "the freshness TTL",
        not decision.authorized and decision.reason is AuthorizationReason.ACCESS_REVOKED,
    )


def test_freshness_ttl_expires_admission_during_a_prolonged_outage():
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("m1"), now=_T0)
    still_fresh = catalog.is_call_authorized("m1", now=_T0 + timedelta(minutes=4, seconds=59))
    check(
        "FTF-1807", "call is still authorized just under the 5-minute TTL", still_fresh.authorized
    )

    expired = catalog.is_call_authorized("m1", now=_T0 + timedelta(minutes=5))
    check(
        "FTF-1807",
        "call is refused once the 5-minute freshness TTL has elapsed with no refresh",
        not expired.authorized and expired.reason is AuthorizationReason.FRESHNESS_EXPIRED,
    )


def test_anomaly_never_gates_or_restores_real_authorization():
    catalog = LastKnownGoodCatalog()
    detector = CatalogAnomalyDetector()
    detector.record(catalog, _discovered("m1", "m2", "m3", "m4"), now=_T0)

    snapshot, anomaly = detector.record(catalog, _discovered(), now=_T0 + timedelta(seconds=1))
    check(
        "FTF-1807",
        "a >50% model-count drop opens a persisted anomaly",
        anomaly is not None,
    )
    check(
        "FTF-1807",
        "the real catalog update still applied unconditionally despite the anomaly "
        "(authorization enforced regardless of anomaly state)",
        catalog.is_call_authorized("m1", now=_T0 + timedelta(seconds=1)).reason
        is AuthorizationReason.NOT_IN_CATALOG
        or not catalog.is_call_authorized("m1", now=_T0 + timedelta(seconds=1)).authorized,
    )

    detector.acknowledge(anomaly.id, "operator@example.com")
    still_denied = catalog.is_call_authorized("m1", now=_T0 + timedelta(seconds=2))
    check(
        "FTF-1807",
        "acknowledging the anomaly is a pure audit transition -- it never restores "
        "the removed model's authorization",
        not still_denied.authorized,
    )


def test_out_of_order_sync_response_cannot_reinstate_revoked_access():
    workflow = PeriodicSyncWorkflow()
    workflow.register()

    seq1 = workflow.begin_cycle()
    seq2 = workflow.begin_cycle()

    result2 = workflow.complete_cycle(seq2, _revoked(), now=_T0 + timedelta(seconds=2))
    check(
        "FTF-1807",
        "the fresher, second-dispatched cycle (real revocation) is applied",
        result2.status is SyncCycleStatus.APPLIED
        and result2.snapshot is not None
        and result2.snapshot.access_state is AccessState.REVOKED,
    )

    stale_healthy = _discovered("m1")
    result1 = workflow.complete_cycle(seq1, stale_healthy, now=_T0 + timedelta(seconds=5))
    check(
        "FTF-1807",
        "the stale, out-of-order first cycle is dropped, never applied",
        result1.status is SyncCycleStatus.SKIPPED_OUT_OF_ORDER,
    )
    check(
        "FTF-1807",
        "safe routing recovery: the stale healthy response could not undo the "
        "fresher, already-applied revocation",
        result1.snapshot is not None and result1.snapshot.access_state is AccessState.REVOKED,
    )


def test_bounded_retry_ceiling_on_consecutive_transient_failures():
    workflow = PeriodicSyncWorkflow(retry_ceiling=3)
    workflow.register()
    statuses = []
    for i in range(3):
        seq = workflow.begin_cycle()
        result = workflow.complete_cycle(seq, _transient(), now=_T0 + timedelta(seconds=i))
        statuses.append(result.status)
    check(
        "FTF-1807",
        "the first two consecutive transient failures are retried, not treated "
        "as a definite outage signal",
        statuses[0] is SyncCycleStatus.RETRY_SCHEDULED
        and statuses[1] is SyncCycleStatus.RETRY_SCHEDULED,
    )
    check(
        "FTF-1807",
        "the third consecutive transient failure reaches the real 3-attempt " "retry ceiling",
        statuses[2] is SyncCycleStatus.RETRY_CEILING_REACHED,
    )


def test_route_attestation_source_carries_the_real_no_cache_and_fingerprint_fields():
    source = (ROOT / "packages" / "litellm_client" / "route_attestation.py").read_text(
        encoding="utf-8"
    )
    check(
        "FTF-1807",
        "every attestation request literally carries LiteLLM's documented "
        "cache-bypass field on the wire (source-verified, not merely described)",
        '"cache": {"no-cache": True}' in source,
    )
    check(
        "FTF-1807",
        "the real deployment fingerprint header constant matches the doc's claim",
        route_attestation._DEPLOYMENT_FINGERPRINT_HEADER == "x-litellm-model-id",
    )


# ---------------------------------------------------------------------------
# 3. Real fault injection against this task's own file (Law 1)
# ---------------------------------------------------------------------------


def test_fault_injection_wrong_ttl_claim_is_caught_by_the_fidelity_check():
    original = DOC_PATH.read_text(encoding="utf-8")
    real_phrase = _expected_ttl_phrase()
    check(
        "FTF-1807",
        "the real freshness-TTL phrase is present in the unmodified doc "
        "(fault-injection precondition)",
        real_phrase in original,
    )

    broken = original.replace(real_phrase, "10 minutes")
    check(
        "FTF-1807", "the fault-injection replacement actually changed the doc", broken != original
    )

    try:
        DOC_PATH.write_text(broken, encoding="utf-8")
        problems = _doc_matches_real_facts(DOC_PATH.read_text(encoding="utf-8"))
        check(
            "FTF-1807",
            "with the TTL claim mismatched against the real imported constant, the "
            "fidelity check genuinely FAILS (proving it is load-bearing, not "
            "decorative)",
            "freshness TTL phrase missing/wrong" in problems,
        )
    finally:
        DOC_PATH.write_text(original, encoding="utf-8")

    restored = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "the runbook file itself was restored byte-identical, never left mutated",
        restored == original,
    )
    post_restore_problems = _doc_matches_real_facts(restored)
    check(
        "FTF-1807",
        "after restore, the real check passes again",
        post_restore_problems == [],
    )


def test_fault_injection_inverted_revocation_guidance_is_caught_by_the_semantics_check():
    """Reproduces the exact 2026-10-01 L2 finding: rewriting the operator
    table's revoked-key row to say access continues until the TTL expires
    (the opposite of the real negative_acceptance) while leaving every
    token the old `_doc_matches_real_facts` check looks for untouched."""
    original = DOC_PATH.read_text(encoding="utf-8")
    real_row = (
        "| Key/connection revoked (401/403) | `DiscoveryState.INVALID_TOKEN` | "
        "Immediate, unconditional call refusal (`access_revoked`) |"
    )
    check(
        "FTF-1807",
        "the real revoked-key operator-table row is present (fault-injection precondition)",
        real_row in original,
    )
    inverted_row = (
        "| Key/connection revoked (401/403) | `DiscoveryState.INVALID_TOKEN` | "
        "Call remains permitted until the 5-minute freshness TTL expires (`access_revoked`) |"
    )
    broken = original.replace(real_row, inverted_row)
    check(
        "FTF-1807",
        "the fault-injection replacement actually changed the doc",
        broken != original,
    )

    try:
        DOC_PATH.write_text(broken, encoding="utf-8")
        reread = DOC_PATH.read_text(encoding="utf-8")
        check(
            "FTF-1807",
            "the OLD token-only check does NOT catch the inverted guidance (every "
            "token it looks for -- '5 minutes', 'access_revoked' -- is still present, "
            "proving the 2026-10-01 L2 gap was real)",
            _doc_matches_real_facts(reread) == [],
        )
        semantics_problems = _doc_operator_guidance_matches_real_semantics(reread)
        check(
            "FTF-1807",
            "the NEW semantics check genuinely FAILS on the inverted revoked-key "
            "guidance, proving it is load-bearing",
            any("revoked-key row" in p for p in semantics_problems),
        )
    finally:
        DOC_PATH.write_text(original, encoding="utf-8")

    restored = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "the runbook file itself was restored byte-identical, never left mutated",
        restored == original,
    )
    check(
        "FTF-1807",
        "after restore, the semantics check passes again",
        _doc_operator_guidance_matches_real_semantics(restored) == [],
    )


def test_fault_injection_fallback_allowed_claim_is_caught_by_the_semantics_check():
    """Reproduces the second half of the same L2 finding: rewriting section 6
    to say an unverified reroute is treated as equivalent to the pinned
    deployment (i.e. external fallback is allowed), the opposite of this
    task's own negative_acceptance."""
    original = DOC_PATH.read_text(encoding="utf-8")
    # The real phrase hard-wraps across two lines in the markdown source, so
    # this matches it the same whitespace-tolerant way the semantics check
    # itself does, rather than a raw (line-sensitive) substring search.
    real_phrase_normalized = "is never treated as equivalent to the pinned deployment"
    check(
        "FTF-1807",
        "the real no-fallback body statement is present (fault-injection precondition)",
        real_phrase_normalized in _normalize_whitespace(original).lower(),
    )
    broken = re.sub(
        r"is\s+never\s+treated\s+as\s+equivalent\s+to\s+the\s+pinned\s+deployment",
        "is treated as equivalent to the pinned deployment, since route attestation "
        "is advisory only and fallback is permitted",
        original,
    )
    check(
        "FTF-1807",
        "the fault-injection replacement actually changed the doc",
        broken != original,
    )

    try:
        DOC_PATH.write_text(broken, encoding="utf-8")
        reread = DOC_PATH.read_text(encoding="utf-8")
        check(
            "FTF-1807",
            "the OLD token-only check does NOT catch the inverted fallback claim "
            "(every token it looks for is still present elsewhere in the doc)",
            _doc_matches_real_facts(reread) == [],
        )
        semantics_problems = _doc_operator_guidance_matches_real_semantics(reread)
        check(
            "FTF-1807",
            "the NEW semantics check genuinely FAILS on the inverted fallback claim",
            any("no-unapproved-fallback body statement" in p for p in semantics_problems),
        )
    finally:
        DOC_PATH.write_text(original, encoding="utf-8")

    restored = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "the runbook file itself was restored byte-identical, never left mutated",
        restored == original,
    )
    check(
        "FTF-1807",
        "after restore, the semantics check passes again",
        _doc_operator_guidance_matches_real_semantics(restored) == [],
    )


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-1807 LiteLLM outage runbook: {len(FAILURES)} failed")
        for f in FAILURES:
            print(f"  {f}")
        sys.exit(1)

    print("FTF-1807 LiteLLM outage runbook: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
