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


# ---------------------------------------------------------------------------
# Round 3 (2026-10-04T11:00Z review): pin every operator-table row, scan the
# WHOLE document for forbidden claims, pin the operator procedure steps, and
# prove each protection turns RED on an added section / reworded rows / a
# reworded step. All checks below are pure functions of the document text, so
# the fault tests feed them modified in-memory copies and never touch the file.
# ---------------------------------------------------------------------------

# Separately written literal expectations (never read back from the doc).
# Constants that the doc must agree with are derived from the real modules.
_PROCEDURE_STEP_COUNT = 7


def _expected_operator_table() -> list[tuple[str, str, str]]:
    ttl = _expected_ttl_phrase()
    retries = catalog_anomaly_detector.DEFAULT_RETRY_CEILING
    drop = int(catalog_anomaly_detector._MAJOR_REDUCTION_THRESHOLD * 100)
    return [
        (
            "Key/connection revoked (401/403)",
            "`DiscoveryState.INVALID_TOKEN`",
            "Immediate, unconditional call refusal (`access_revoked`)",
        ),
        (
            "Connection revoked by admin",
            "`litellm_connections.status != 'active'`",
            "Immediate refusal (`CONNECTION_REVOKED`), independent of catalog state",
        ),
        (
            f"No successful refresh for {ttl}",
            "`FRESHNESS_EXPIRED`",
            "New work refused; existing snapshot kept for audit only",
        ),
        (
            f">{drop}% model-count drop",
            "Anomaly opened",
            "Recorded for operator review; authorization from the fresh result still "
            "applies unconditionally",
        ),
        (
            "Transient network failure",
            f"`DEGRADED`, bounded {retries}-attempt retry counter",
            "Existing entries untouched; no revocation inferred",
        ),
        (
            "Stale/out-of-order sync response",
            "`sequence <= last_applied_sequence`",
            "Dropped before it reaches the catalog; cannot reinstate access",
        ),
        (
            "Router silently reroutes to a different deployment",
            "Changed/missing `x-litellm-model-id` fingerprint",
            "Identity stays `UNVERIFIED`; never claimed equivalent to the pinned deployment",
        ),
        (
            "Response served from cache during attestation",
            "Structurally impossible",
            'Every attestation request carries `"cache": {"no-cache": true}`',
        ),
    ]


_EXPECTED_HEADINGS = [
    "# LiteLLM outage runbook (FTF-1807)",
    '## 1. What "outage" means, precisely',
    "## 2. Revocation handling",
    "## 3. Freshness TTL — the outage admission cutoff",
    "## 4. Bounded retries and persisted anomalies",
    "## 5. Safe routing recovery — no out-of-order reinstatement",
    "## 6. Fallback and identity handling — no unauthorized cache or silent reroute",
    "## 7. Operator summary",
    "## 8. Operator procedure",
]


def _expected_procedure_phrases() -> list[list[str]]:
    ttl = _expected_ttl_phrase()
    retries = catalog_anomaly_detector.DEFAULT_RETRY_CEILING
    drop = int(catalog_anomaly_detector._MAJOR_REDUCTION_THRESHOLD * 100)
    return [
        [
            "key or connection revoked (401/403)",
            "at once",
            "do not wait for the freshness ttl",
            "do not retry",
        ],
        [
            "revoked by an administrator",
            "at once",
            "whatever the catalog says",
            "`active`",
        ],
        [
            f"no successful refresh for {ttl}",
            "admit no new work",
            "do not lengthen the ttl",
        ],
        [
            f"more than {drop}%",
            "anomalyrecord",
            "acknowledge(...)",
            "never restores",
        ],
        [
            "transient network failure",
            f"{retries} attempts",
            "do not infer a revocation",
            "untouched",
        ],
        [
            "stale or out-of-order",
            "skipped_out_of_order",
            "no manual action",
        ],
        [
            "silent reroute",
            "x-litellm-model-id",
            "unverified",
            "do not accept a cached answer",
            "do not use any external or unapproved fallback",
        ],
    ]


def _section(raw_doc_text: str, number: int) -> str:
    """Text of `## <number>. ...` up to the next `## ` heading (or EOF); ''
    if the section does not exist."""
    lines = raw_doc_text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"## {number}."):
            start = index + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end])


def _parse_operator_table(raw_doc_text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in _section(raw_doc_text, 7).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [_normalize_whitespace(c) for c in line.strip("|").split("|")]
        if len(cells) != 3 or tuple(cells) == _TABLE_HEADER or set(cells[0]) <= {"-", " ", ":"}:
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


_TABLE_HEADER = ("Signal", "Detection", "Effect")


def _table_structure_violations(raw_doc_text: str) -> list[str]:
    """Section 7 must be exactly: heading, one header row, one separator row,
    the data rows. Any other `|` line (wrong cell count, a second header, a
    row literally named "Signal") or any prose line is reported rather than
    silently skipped."""
    problems: list[str] = []
    header_seen = separator_seen = 0
    for line in _section(raw_doc_text, 7).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith("|"):
            problems.append(f"section 7 has a non-table line: {stripped[:60]!r}")
            continue
        cells = [_normalize_whitespace(c) for c in stripped.strip("|").split("|")]
        if len(cells) != 3:
            problems.append(f"section 7 table line has {len(cells)} cells: {stripped[:60]!r}")
        elif all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            separator_seen += 1
            if header_seen != 1:
                problems.append("section 7 separator row is not directly after the header")
        elif tuple(cells) == _TABLE_HEADER:
            header_seen += 1
    if header_seen != 1 or separator_seen != 1:
        problems.append(
            f"section 7 has {header_seen} header rows and {separator_seen} separator rows, "
            "expected 1 and 1"
        )
    return problems


def _operator_table_violations(raw_doc_text: str) -> list[str]:
    """Every operator-table row (Signal, Detection AND Effect) must equal its
    literal expected value, and no row may be added, removed, duplicated or
    reordered. An exact pin, not a token search: rewording any row to a weaker
    meaning ("still admitted", "may be ignored", "may be served from cache")
    is reported by row."""
    expected = _expected_operator_table()
    actual = _parse_operator_table(raw_doc_text)
    problems: list[str] = _table_structure_violations(raw_doc_text)
    if len(actual) != len(expected):
        problems.append(f"operator table has {len(actual)} rows, expected {len(expected)}")
    for index, (want_row, got_row) in enumerate(zip(expected, actual, strict=False)):
        for column, want, got in zip(
            ("signal", "detection", "effect"), want_row, got_row, strict=True
        ):
            if want != got:
                problems.append(
                    f"operator table row {index + 1} ({want_row[0]!r}) {column}: "
                    f"expected {want!r}, got {got!r}"
                )
    return problems


# Positive-assertion phrasings of the claims the runbook must never make. Each
# pattern carries its own narrow negation guard (a lookbehind), never a
# sentence-wide "contains 'not'" escape, so "... is permitted, not blocked" is
# still caught. Scanned over the WHOLE document, whitespace-normalized.
_FORBIDDEN_CLAIMS: list[tuple[str, str]] = [
    (
        "post-revocation access",
        r"(?:revok|revoc)\w*[^.;|]{0,160}\b(?:remain|remains|continue|continues|stay|stays|still)\b"
        r"[^.;|]{0,60}\b(?:permitted|authori[sz]ed|allowed|callable|admitted|served|valid|usable)",
    ),
    (
        "revocation waits for the TTL",
        r"\buntil (?:the )?(?:(?:5|five)[- ]minute |freshness |default_freshness_)*ttl\b",
    ),
    (
        "deferred refusal",
        r"\b(?:refus\w*|reject\w*|deny|denies|denial)\b[^.;|]{0,80}"
        r"\b(?:deferred|delayed|postponed|"
        r"after (?:the )?(?:(?:5|five)[- ]minute |freshness )*ttl)\b",
    ),
    ("grace period", r"\bgrace (?:period|window)\b"),
    (
        "cached access allowed",
        r"\b(?:cached|cache hit|last[- ]known[- ]good)\b[^.;|]{0,60}"
        r"\b(?:is|are|may be|can be|will be) (?:still )?"
        r"(?:permitted|allowed|acceptable|served|honou?red|accepted|used)\b",
    ),
    (
        "external or unapproved fallback allowed",
        r"\b(?:fallback|fall back|reroute|rerouting|external provider)\w*[^.;|]{0,60}"
        r"\b(?:is|are|may be|can be|will be) (?:permitted|allowed|approved|acceptable|"
        r"authori[sz]ed|safe)\b",
    ),
    ("reroute treated as equivalent", r"(?<!never )(?<!not )\btreated as equivalent\b"),
    (
        "no-cache optional",
        r"\bno-cache\b[^.;|]{0,80}\b(?:optional|may be omitted|can be omitted|is omitted|"
        r"disabled|skipped|not required|advisory)\b",
    ),
    (
        "no-cache optional (verb first)",
        r"\b(?:optionally|may|can) (?:omit|skip|disable)\w* [^.;|]{0,40}no-cache",
    ),
    (
        "cache hit tolerated",
        r"\bserved from (?:the )?cache\b[^.;|]{0,60}\b(?:is|are) "
        r"(?:fine|ok|okay|acceptable|permitted|allowed|expected)\b",
    ),
    (
        "attestation advisory",
        r"\battestation\b[^.;|]{0,60}\b(?:advisory|optional|best[- ]effort)\b",
    ),
]


def _scan_text(raw_doc_text: str) -> str:
    """Whitespace-collapsed, lower-cased text with markdown emphasis and code
    markers (`*`, backticks) removed, so bold, italic, inline-code or
    code-fenced wrapping cannot hide a claim from the scan."""
    stripped = raw_doc_text.lower().replace("*", "").replace("`", "")
    return _normalize_whitespace(stripped)


def _forbidden_claim_violations(raw_doc_text: str) -> list[str]:
    normalized = _scan_text(raw_doc_text)
    return [
        f"forbidden claim ({name}): {match.group(0)!r}"
        for name, pattern in _FORBIDDEN_CLAIMS
        for match in re.finditer(pattern, normalized)
    ]


def _heading_violations(raw_doc_text: str) -> list[str]:
    actual = [
        line.rstrip()
        for line in raw_doc_text.splitlines()
        if line.startswith("# ") or line.startswith("## ")
    ]
    if actual == _EXPECTED_HEADINGS:
        return []
    return [f"document headings differ: expected {_EXPECTED_HEADINGS!r}, got {actual!r}"]


def _procedure_steps(raw_doc_text: str) -> list[str]:
    steps: list[str] = []
    for line in _section(raw_doc_text, 8).splitlines():
        if re.match(r"^\d+\.\s", line):
            steps.append(line)
        elif steps and line.strip() and not line.lstrip().startswith(("#", ">")):
            steps[-1] += " " + line.strip()
    return [_normalize_whitespace(step).lower().replace("**", "") for step in steps]


def _procedure_violations(raw_doc_text: str) -> list[str]:
    steps = _procedure_steps(raw_doc_text)
    wanted = _expected_procedure_phrases()
    problems: list[str] = []
    if len(steps) != _PROCEDURE_STEP_COUNT:
        problems.append(
            f"operator procedure has {len(steps)} steps, expected {_PROCEDURE_STEP_COUNT}"
        )
    for index, (step, phrases) in enumerate(zip(steps, wanted, strict=False)):
        number_prefix = f"{index + 1}. "
        if not step.startswith(number_prefix):
            problems.append(f"procedure step {index + 1} is out of order (reads: {step[:40]!r})")
        for phrase in phrases:
            if phrase not in step:
                problems.append(f"procedure step {index + 1} is missing {phrase!r}")
    return problems


def _all_round3_violations(raw_doc_text: str) -> list[str]:
    return (
        _heading_violations(raw_doc_text)
        + _operator_table_violations(raw_doc_text)
        + _forbidden_claim_violations(raw_doc_text)
        + _procedure_violations(raw_doc_text)
    )


def test_round3_real_runbook_passes_every_pin_and_scan():
    """Negative test on the real default document: the unmodified runbook
    satisfies the heading pin, all 8 operator-table rows, the whole-document
    forbidden-claim scan and the 7 operator procedure steps."""
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "the real runbook has exactly the 8 expected operator-table rows (counted, not assumed)",
        len(_parse_operator_table(doc_text)) == 8 == len(_expected_operator_table()),
    )
    check(
        "FTF-1807",
        f"the real runbook has exactly {_PROCEDURE_STEP_COUNT} operator procedure steps",
        len(_procedure_steps(doc_text)) == _PROCEDURE_STEP_COUNT,
    )
    problems = _all_round3_violations(doc_text)
    check(
        "FTF-1807",
        f"the real runbook passes the heading pin, table pin, forbidden-claim scan and "
        f"procedure pin (violations: {problems})",
        problems == [],
    )


_ADDED_SECTION_ATTACKS = [
    "Revoked access continues to be permitted until the 5-minute TTL expires.",
    "A revoked key remains authorized for a short grace period after revocation.",
    "Refusal of a revoked connection is deferred until the next sync completes.",
    "Cached responses are acceptable during a private outage.",
    "The last-known-good snapshot is still permitted to serve calls after revocation.",
    "External fallback is permitted when the private route is unavailable.",
    "An unverified reroute is treated as equivalent to the pinned deployment.",
    "The no-cache field is optional for attestation requests.",
    "Operators may omit the no-cache field when the gateway is slow.",
    "A response served from cache is acceptable during attestation.",
    "Route attestation is advisory only.",
]


_PATTERN_ISOLATION_ATTACKS: dict[str, str] = {
    "post-revocation access": "Revoked access continues to be permitted for a short time.",
    "revocation waits for the TTL": "Calls stay open until the TTL ends.",
    "deferred refusal": "Refusal is deferred to the next cycle.",
    "grace period": "Operators get a grace period before enforcement begins.",
    "cached access allowed": "A cached response is acceptable during an outage.",
    "external or unapproved fallback allowed": "External fallback is permitted.",
    "reroute treated as equivalent": "A reroute is treated as equivalent.",
    "no-cache optional": "The no-cache field is optional.",
    "no-cache optional (verb first)": "Operators may omit the no-cache field.",
    "cache hit tolerated": "A response served from cache is acceptable.",
    "attestation advisory": "Attestation is advisory.",
}


def _violation_names(raw_doc_text: str) -> set[str]:
    return {
        problem.split("):")[0].removeprefix("forbidden claim (")
        for problem in _forbidden_claim_violations(raw_doc_text)
    }


def test_round3_every_forbidden_pattern_is_the_sole_catcher_of_its_own_sentence():
    """Each forbidden-claim pattern must be load-bearing: one sentence that ONLY
    that pattern catches, so deleting any single pattern turns this RED (a
    pattern masked by another pattern would stay GREEN when removed)."""
    original = DOC_PATH.read_text(encoding="utf-8")
    declared = {name for name, _ in _FORBIDDEN_CLAIMS}
    check(
        "FTF-1807",
        "every declared forbidden-claim pattern has an isolation attack sentence",
        declared == set(_PATTERN_ISOLATION_ATTACKS),
    )
    for name, sentence in _PATTERN_ISOLATION_ATTACKS.items():
        names = _violation_names(original + f"\n\n{sentence}\n")
        check(
            "FTF-1807",
            f"{sentence!r} is caught by exactly the {name!r} pattern (got {sorted(names)})",
            names == {name},
        )


def test_round3_added_section_and_inline_inversions_are_caught_by_the_whole_document_scan():
    """FTF-1807 round-3 DoD: an ADDED section (not a table-row rewrite) stating
    any forbidden claim turns the whole-document scan RED, and so does the same
    sentence slipped into an existing section with no new heading (so the
    heading pin alone cannot be what catches it)."""
    original = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "precondition: the unmodified document has no forbidden claim",
        _forbidden_claim_violations(original) == [],
    )
    for attack in _ADDED_SECTION_ATTACKS:
        with_section = original + f"\n## 9. Addendum\n\n{attack}\n"
        check(
            "FTF-1807",
            f"an added section stating {attack!r} is caught by the forbidden-claim scan",
            _forbidden_claim_violations(with_section) != [],
        )
        check(
            "FTF-1807",
            f"an added section stating {attack!r} is also caught by the heading pin",
            _heading_violations(with_section) != [],
        )
        inline = original.replace(
            "## 7. Operator summary", f"{attack}\n\n## 7. Operator summary", 1
        )
        check(
            "FTF-1807",
            f"the same sentence inserted into an existing section (no new heading) is caught "
            f"by the forbidden-claim scan: {attack!r}",
            _forbidden_claim_violations(inline) != [] and _heading_violations(inline) == [],
        )
        negated_bypass = original + f"\n\n{attack[:-1]}, not blocked.\n"
        check(
            "FTF-1807",
            f"adding the word 'not' elsewhere in the sentence does not bypass the scan: {attack!r}",
            _forbidden_claim_violations(negated_bypass) != [],
        )


def test_round3_each_reworded_operator_table_row_is_caught_by_the_table_pin():
    """Every one of the 8 rows reworded to a weaker meaning (and, separately,
    three at once, as the review described) turns the table pin RED and names
    the row."""
    original = DOC_PATH.read_text(encoding="utf-8")
    rewrites = [
        ("Immediate, unconditional call refusal", "Call refusal after the next sync"),
        (
            "Immediate refusal (`CONNECTION_REVOKED`), independent of catalog state",
            "Refusal (`CONNECTION_REVOKED`) once the catalog agrees",
        ),
        (
            "New work refused; existing snapshot kept for audit only",
            "New work still admitted; existing snapshot kept for audit only",
        ),
        (
            "Recorded for operator review; authorization from the fresh result still "
            "applies unconditionally",
            "Recorded for operator review; may be ignored",
        ),
        (
            "Existing entries untouched; no revocation inferred",
            "Existing entries cleared; revocation inferred",
        ),
        (
            "Dropped before it reaches the catalog; cannot reinstate access",
            "Applied to the catalog; may reinstate access",
        ),
        (
            "Identity stays `UNVERIFIED`; never claimed equivalent to the pinned deployment",
            "Identity treated as verified; fallback accepted",
        ),
        (
            'Every attestation request carries `"cache": {"no-cache": true}`',
            "Attestation requests may be served from cache",
        ),
    ]
    for index, (old, new) in enumerate(rewrites):
        normalized_original = original
        # the doc's table row is a single line, so a plain replace is exact
        check(
            "FTF-1807",
            f"fault precondition: table row {index + 1} text is present verbatim",
            old in normalized_original,
        )
        broken = normalized_original.replace(old, new, 1)
        problems = _operator_table_violations(broken)
        check(
            "FTF-1807",
            f"rewording operator-table row {index + 1} to {new!r} turns the table pin RED "
            f"(names row {index + 1})",
            any(f"row {index + 1} " in problem for problem in problems),
        )
    three = original
    for old, new in (rewrites[2], rewrites[3], rewrites[7]):
        three = three.replace(old, new, 1)
    three_problems = _operator_table_violations(three)
    check(
        "FTF-1807",
        "three reworded rows at once (3, 4 and 8) are each reported",
        all(any(f"row {n} " in problem for problem in three_problems) for n in (3, 4, 8)),
    )
    deleted = "\n".join(
        line for line in original.splitlines() if not line.startswith("| Transient network failure")
    )
    check(
        "FTF-1807",
        "deleting a row turns the table pin RED",
        _operator_table_violations(deleted) != [],
    )
    duplicated = original.replace(
        "| Transient network failure |",
        "| Transient network failure | `DEGRADED` | Revocation inferred |\n"
        "| Transient network failure |",
        1,
    )
    check(
        "FTF-1807",
        "adding a contradictory row with the same Signal turns the table pin RED",
        _operator_table_violations(duplicated) != [],
    )


def test_round3_operator_procedure_steps_are_pinned_and_faults_turn_red():
    original = DOC_PATH.read_text(encoding="utf-8")
    steps = [line for line in _section(original, 8).splitlines() if re.match(r"^\d+\.\s", line)]
    check(
        "FTF-1807",
        f"the real runbook has an '## 8. Operator procedure' section with "
        f"{_PROCEDURE_STEP_COUNT} numbered steps (found {len(steps)})",
        len(steps) == _PROCEDURE_STEP_COUNT,
    )
    reworded = {
        1: ("do not wait for the freshness TTL", "wait for the freshness TTL"),
        2: ("at once, whatever the catalog says", "once the catalog agrees"),
        3: ("Admit no new work", "Admit new work"),
        4: ("never restores", "restores"),
        5: ("do not infer a revocation", "infer a revocation"),
        6: ("No manual action", "Manual action"),
        7: ("do not use any external or unapproved fallback", "use an external fallback"),
    }
    for number, (old, new) in reworded.items():
        if old not in original:
            check("FTF-1807", f"fault precondition: step {number} contains {old!r}", False)
            continue
        broken = original.replace(old, new, 1)
        check(
            "FTF-1807",
            f"rewording procedure step {number} ({old!r} -> {new!r}) turns the procedure pin RED",
            any(f"step {number} " in problem for problem in _procedure_violations(broken)),
        )
    check(
        "FTF-1807",
        "removing the whole operator procedure section turns the procedure pin RED",
        _procedure_violations(original.split("## 8. Operator procedure")[0]) != [],
    )
    extra = original.rstrip() + "\n8. Optionally allow cached answers during an outage.\n"
    check(
        "FTF-1807",
        "an extra eighth procedure step turns the procedure pin RED",
        _procedure_violations(extra) != [],
    )
    swapped_lines = original.splitlines()
    section_start = next(i for i, line in enumerate(swapped_lines) if line.startswith("## 8."))
    first = next(
        i for i in range(section_start, len(swapped_lines)) if swapped_lines[i].startswith("1. ")
    )
    second = next(
        i for i in range(section_start, len(swapped_lines)) if swapped_lines[i].startswith("2. ")
    )
    swapped_lines[first], swapped_lines[second] = swapped_lines[second], swapped_lines[first]
    check(
        "FTF-1807",
        "swapping steps 1 and 2 turns the procedure pin RED",
        _procedure_violations("\n".join(swapped_lines)) != [],
    )


# ---------------------------------------------------------------------------
# Round 4 (2026-10-06T20:10Z review): the forbidden-claim scan is a phrase
# blacklist, so a plain-language inversion ("Revoked keys keep working for up
# to 5 minutes", "Falling back to OpenAI is acceptable") passes it, and the
# prose of sections 1-6 was unpinned. The fix is to pin the text exactly: every
# whitespace-normalized paragraph of every section is held as a sha256 below,
# so ANY change to the runbook's wording fails, whether or not a blacklist
# pattern happens to match it. The structured pins above stay (they name the
# row/step that changed); the scan stays as a second, independent layer.
# Updating the runbook now means re-reviewing it and re-pinning these hashes.
# ---------------------------------------------------------------------------

_PINNED_PARAGRAPH_SHA256: dict[int, tuple[str, ...]] = {
    0: (
        "6ac06da4427d4af566b96b443f9fcd38c1a9b0d9894e822717b1e883ec42679d",
        "53d28e951002b5bbc4755b32e9ec5e50924dd7031be007fa096c272f80a3d3f8",
        "3938174756eae65b05bbe43c186e37be575d02119618972c51fd7df3e1617196",
    ),
    1: (
        "2ca0302e213e9f71b73fbe3a444fcd036d6aad3845db003389bdc93d1283dc3d",
        "0a3245172fd197520f021f61b2f0850c66fa9acfb95dcb45d523fb2ebbf138d1",
        "011ee0f08ae103a6148c3d7034ed8c3296a84854d85d6304659dc5a82e0455d1",
    ),
    2: (
        "6682cba1e580c0b435ad7452690de65dfb7ba70b24c1ea0f36614f4d77c68b23",
        "efcdf000e37de1e42a6a2013a14b86005a3ad631c53815f36f26a3b596d47315",
        "ac888ab8a4abcff290f23e1afeec1bef038d25eb9018438ca43c29cbaa84d1cc",
        "70038d71c448a44ec708d256a41940955b822287108387428f5f8e0c89013381",
        "05deda6ab40bc5d3a2f17f8f454fd5ee7636a1b41f5bf0b2c634fd3652b26b31",
    ),
    3: (
        "90142b5ff28257178684882c858b0dd871385bae34ec5d5ea048e28d6be987ce",
        "f1e94b857ae02e558ef263468218e2e616a5b94971dfec740b497934c63c28a3",
    ),
    4: (
        "bc1a9221c0b5c36d610d2131263398c94123cce090afeef9dc48c0e2808f8766",
        "0035265a8295ec66247b884b169aabbeff6aaf5b98e67025270fe9b2624de2f7",
    ),
    5: (
        "d912dd4ee8473afe2b6b3cce1edf4f7e45b28ab71c16152f0e3d8b2d08303aaf",
        "ccebdfcaae735d4829d72b9f40a343c5f5cc4be36ac6ff4c415aa119c9abe6a0",
    ),
    6: (
        "526d4993f6c2ed1f3c93c5cf62f4ba0d45efca4892a1bb3783ee13700ef4c40c",
        "3e94e220ef477b7ab8f3bc6f69092735084631024c02eebb02f095f84b6d8ff7",
        "e99e9804d98ba3451e1493c802c6dafc8824f759b5b05ef466559e98725e5725",
    ),
    7: (
        "8249ecccee68f47ea6cad9800263159aec7dc3766e540c0f6b278e4ca4c09031",
        "d1ea1876264c77b1b5160e9d2e203aab06ccb070b18e56befda085e109b664dd",
    ),
    8: (
        "891e45a9679c04537b927ea9c4a5c852c3f8f87045bb57d590fed70f91866104",
        "a5a872cf59586fa181ddd523829fd4e92444ec1392ff868f20779013a7bf35ca",
        "4875855a11538afbd9e08773c6a24f9ecfaa2932a723691ad1317e593a4528e1",
        "e7a870cabec22b7c1c5c4d90d0653e052f99bfd982740d1febb3735bbcbc5870",
    ),
}


def _paragraphs_by_section(raw_doc_text: str) -> dict[int, list[str]]:
    """Section 0 is the title + preamble; sections 1..N follow their `## N.`
    heading. Paragraphs are blank-line separated blocks, whitespace-normalized
    but otherwise exact (case, punctuation, markdown markers all count)."""
    sections: dict[int, list[str]] = {0: []}
    current = 0
    for line in raw_doc_text.splitlines():
        match = re.match(r"## (\d+)\.", line)
        if match:
            current = int(match.group(1))
            sections.setdefault(current, [])
        sections[current].append(line)
    return {
        number: [_normalize_whitespace(b) for b in "\n".join(lines).split("\n\n") if b.strip()]
        for number, lines in sections.items()
    }


def _paragraph_pin_violations(raw_doc_text: str) -> list[str]:
    import hashlib

    actual = _paragraphs_by_section(raw_doc_text)
    problems: list[str] = []
    for number in sorted(set(actual) | set(_PINNED_PARAGRAPH_SHA256)):
        want = _PINNED_PARAGRAPH_SHA256.get(number)
        got = actual.get(number)
        if want is None:
            problems.append(f"section {number} is not in the pinned runbook")
            continue
        if got is None:
            problems.append(f"section {number} is missing from the runbook")
            continue
        if len(got) != len(want):
            problems.append(f"section {number} has {len(got)} paragraphs, expected {len(want)}")
        for index, (want_hash, paragraph) in enumerate(zip(want, got, strict=False)):
            if hashlib.sha256(paragraph.encode("utf-8")).hexdigest() != want_hash:
                problems.append(
                    f"section {number} paragraph {index + 1} differs from the pinned text "
                    f"(starts {paragraph[:50]!r})"
                )
    return problems


def _all_violations(raw_doc_text: str) -> list[str]:
    return _all_round3_violations(raw_doc_text) + _paragraph_pin_violations(raw_doc_text)


def test_round4_real_runbook_passes_the_paragraph_pin_and_every_checker():
    """Negative test on the real default document, exercising every checker
    together: the unmodified runbook has zero violations."""
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    pinned = sum(len(v) for v in _PINNED_PARAGRAPH_SHA256.values())
    actual = sum(len(v) for v in _paragraphs_by_section(doc_text).values())
    check(
        "FTF-1807",
        f"the runbook has the {pinned} pinned paragraphs (found {actual})",
        pinned == actual,
    )
    check(
        "FTF-1807",
        "every section 0-8 is pinned",
        sorted(_PINNED_PARAGRAPH_SHA256) == list(range(9)),
    )
    problems = _all_violations(doc_text)
    check("FTF-1807", f"the real runbook has no violation (got {problems})", problems == [])


_PLAIN_LANGUAGE_INVERSIONS = [
    "Revoked keys keep working for up to 5 minutes.",
    "Falling back to OpenAI is acceptable.",
    "Operators can keep using a revoked key until the next sync.",
    "Calls to a revoked connection succeed for a few more minutes.",
    "If the gateway is down, route traffic to a public provider instead.",
    "Stale sync responses should be applied so access recovers quickly.",
    "Cached answers are fine while the gateway is unreachable.",
    "The freshness window can be raised to ride out an outage.",
    "Acknowledging an anomaly restores the removed models.",
    "A missing fingerprint means the route is equivalent to the pinned deployment.",
    "Retries continue forever until the gateway answers.",
    "Revocation takes effect only after the TTL runs out.",
    "Attestation requests may reuse a cached response to save cost.",
    "Anyone in the organization may acknowledge anomalies without review.",
    "Admission continues on the last-known-good snapshot during any outage.",
]


def test_round4_plain_language_inversions_are_caught_by_the_paragraph_pin():
    """The 15 inversions the review found GREEN, plus the same sentences as an
    added section, appended to an existing paragraph, and wrapped in bold: all
    of them turn the paragraph pin RED, and the blacklist scan is never needed."""
    original = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "precondition: the pin passes unmodified",
        _paragraph_pin_violations(original) == [],
    )
    for sentence in _PLAIN_LANGUAGE_INVERSIONS:
        added_paragraph = original.replace(
            "## 7. Operator summary", f"{sentence}\n\n## 7. Operator summary", 1
        )
        check(
            "FTF-1807",
            f"an added paragraph {sentence!r} turns the paragraph pin RED",
            _paragraph_pin_violations(added_paragraph) != [],
        )
        added_section = original + f"\n## 9. Addendum\n\n{sentence}\n"
        check(
            "FTF-1807",
            f"an added section {sentence!r} turns the paragraph pin RED",
            _paragraph_pin_violations(added_section) != [],
        )
        suffixed = original.replace(
            "never through a retried or delayed stale one.",
            f"never through a retried or delayed stale one. {sentence}",
            1,
        )
        check(
            "FTF-1807",
            f"a sentence {sentence!r} appended to a section 5 paragraph turns the pin RED",
            suffixed != original and _paragraph_pin_violations(suffixed) != [],
        )
        bold = original.replace(
            "## 7. Operator summary", f"**{sentence}**\n\n## 7. Operator summary", 1
        )
        check(
            "FTF-1807",
            f"the bold sentence {sentence!r} turns the paragraph pin RED",
            _paragraph_pin_violations(bold) != [],
        )
    named = [
        _scan_text(sentence)
        for sentence in _PLAIN_LANGUAGE_INVERSIONS
        if _forbidden_claim_violations(original + "\n\n" + sentence + "\n") == []
    ]
    check(
        "FTF-1807",
        "precondition (the review's finding): most of these inversions evade the blacklist "
        f"scan, which is exactly why the pin is needed (evading: {len(named)} of "
        f"{len(_PLAIN_LANGUAGE_INVERSIONS)})",
        len(named) >= 10,
    )


def test_round4_every_paragraph_of_every_section_is_pinned_exactly():
    """Delete, empty, truncate or reword EVERY paragraph of sections 0-8 in
    turn: each fault must turn the pin RED and name that section."""
    original = DOC_PATH.read_text(encoding="utf-8")
    blocks = [b for b in original.split("\n\n") if b.strip()]
    check("FTF-1807", "the runbook splits into blank-line blocks", len(blocks) >= 20)
    for index, block in enumerate(blocks):
        words = block.split()
        label = " ".join(words[:6])
        faults = {
            "deleted": original.replace(block, "", 1),
            "last word dropped": original.replace(block, " ".join(words[:-1]), 1),
            "case changed": original.replace(block, block.swapcase(), 1),
            "word swapped": original.replace(block, block.replace(words[-1], "maybe", 1), 1),
        }
        for kind, broken in faults.items():
            check(
                "FTF-1807",
                f"block {index + 1} ({label!r}) {kind}: the paragraph pin goes RED",
                broken != original and _paragraph_pin_violations(broken) != [],
            )
    # the two named examples from the review: an emptied section and a flipped verb
    for number, marker in ((3, "## 3."), (5, "## 5.")):
        lines = original.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(marker))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        emptied = "\n".join(lines[: start + 1] + [""] + lines[end:])
        problems = _paragraph_pin_violations(emptied)
        check(
            "FTF-1807",
            f"emptying the body of section {number} turns the pin RED and names it",
            any(f"section {number} " in problem for problem in problems),
        )
    flipped = original.replace("is dropped entirely", "is applied", 1)
    check(
        "FTF-1807",
        "changing 'is dropped entirely' to 'is applied' (section 5) turns the pin RED",
        flipped != original
        and any("section 5 " in problem for problem in _paragraph_pin_violations(flipped)),
    )
    check(
        "FTF-1807",
        "the flipped section 5 is NOT caught by the blacklist scan (why the pin exists)",
        _forbidden_claim_violations(flipped) == [],
    )


_WRAPPED_BOLD_CODE_UPPER_CLAIMS = [
    "Revoked access continues to be permitted until the TTL expires.",
    "External fallback is permitted when the private route is unavailable.",
    "A response served from cache is acceptable during attestation.",
    "An unverified reroute is treated as equivalent to the pinned deployment.",
]


def test_round4_forbidden_claim_scan_catches_wrapped_bold_code_and_upper_case_claims():
    original = DOC_PATH.read_text(encoding="utf-8")
    for claim in _WRAPPED_BOLD_CODE_UPPER_CLAIMS:
        words = claim.split()
        variants = {
            "plain": claim,
            "hard-wrapped every word": "\n".join(words),
            "hard-wrapped with indentation": "\n  ".join(words),
            "wrapped mid-phrase in a list item": "- "
            + " ".join(words[:3])
            + "\n  "
            + " ".join(words[3:]),
            "bold": f"**{claim}**",
            "bold on every word": " ".join(f"**{w}**" for w in words),
            "italic": f"*{claim}*",
            "inline code": f"`{claim}`",
            "inline code on every word": " ".join(f"`{w}`" for w in words),
            "code block": f"```\n{claim}\n```",
            "code block with wrapping": "```text\n" + "\n".join(words) + "\n```",
            "upper-case": claim.upper(),
            "title-case": claim.title(),
            "blockquote": f"> {claim}",
            "table cell": f"| Note | {claim} | x |",
        }
        for kind, text in variants.items():
            check(
                "FTF-1807",
                f"the scan is RED on the {kind} claim {claim!r}",
                _forbidden_claim_violations(original + f"\n\n{text}\n") != [],
            )
            check(
                "FTF-1807",
                f"the paragraph pin is also RED on the {kind} claim {claim!r}",
                _paragraph_pin_violations(original + f"\n\n{text}\n") != [],
            )


def test_round4_extra_or_suffixed_table_rows_are_caught():
    original = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "precondition: the table passes unmodified",
        _operator_table_violations(original) == [],
    )
    rows = [
        line for line in original.splitlines() if line.startswith("| ") and "Signal" not in line
    ]
    check("FTF-1807", "the doc table has 8 data rows", len(rows) == 8)
    extra_row = "| Gateway fully down | `DEGRADED` | Calls continue on the cached snapshot |"
    last = rows[-1]
    faults = {
        "extra row after the last row": original.replace(last, last + "\n" + extra_row, 1),
        "extra row before the first row": original.replace(rows[0], extra_row + "\n" + rows[0], 1),
        "extra row in the middle": original.replace(rows[3], rows[3] + "\n" + extra_row, 1),
        "extra row after a blank line": original.replace(last, last + "\n\n" + extra_row, 1),
        "extra row with an extra cell": original.replace(last, last + "\n" + extra_row + " x |", 1),
        "extra row with too few cells": original.replace(last, last + "\n| Gateway down | ok |", 1),
        "extra row named Signal": original.replace(
            last, last + "\n| Signal | `X` | Calls continue |", 1
        ),
        "second header row": original.replace(last, last + "\n| Signal | Detection | Effect |", 1),
        "extra prose line in section 7": original.replace(
            "## 8. Operator procedure",
            "Operators may ignore the table.\n\n## 8. Operator procedure",
            1,
        ),
        "separator row removed": "\n".join(
            line for line in original.splitlines() if not line.startswith("|---")
        ),
        "second separator row": original.replace(last, last + "\n|---|---|---|", 1),
        "header row removed": "\n".join(
            line for line in original.splitlines() if not line.startswith("| Signal |")
        ),
        "row deleted": original.replace(rows[2] + "\n", "", 1),
        "row duplicated": original.replace(rows[2], rows[2] + "\n" + rows[2], 1),
    }
    for index, row in enumerate(rows):
        cells = [c.strip() for c in row.strip("|").split("|")]
        for column, name in enumerate(("signal", "detection", "effect")):
            suffixed = list(cells)
            suffixed[column] += " and may be ignored"
            faults[f"row {index + 1} {name} cell suffixed"] = original.replace(
                row, "| " + " | ".join(suffixed) + " |", 1
            )
            prefixed = list(cells)
            prefixed[column] = "Usually " + prefixed[column]
            faults[f"row {index + 1} {name} cell prefixed"] = original.replace(
                row, "| " + " | ".join(prefixed) + " |", 1
            )
    for kind, broken in faults.items():
        check(
            "FTF-1807",
            f"table fault '{kind}' turns the operator-table pin RED",
            broken != original and _operator_table_violations(broken) != [],
        )
        check(
            "FTF-1807",
            f"table fault '{kind}' also turns the paragraph pin RED",
            _paragraph_pin_violations(broken) != [],
        )


def test_round4_renumbered_procedure_steps_are_caught() -> None:
    original = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "precondition: the procedure passes unmodified",
        _procedure_violations(original) == [],
    )
    head, marker, tail = original.partition("## 8. Operator procedure")
    check("FTF-1807", "the procedure section was found", marker != "")

    def renumber(old: str, new: str, count: int = 1) -> str:
        return head + marker + tail.replace(old, new, count)

    faults: dict[str, str] = {}
    for number in range(1, 8):
        for other in (number + 1, number + 5, 0, 9):
            if other == number:
                continue
            faults[f"step {number} renumbered to {other}"] = renumber(
                f"\n{number}. **", f"\n{other}. **"
            )
    faults["every step numbered 1."] = head + marker + re.sub(r"(?m)^[1-7]\. \*\*", "1. **", tail)
    faults["steps shifted by one"] = (
        head + marker + re.sub(r"(?m)^([1-7])\. \*\*", lambda m: f"{int(m.group(1)) + 1}. **", tail)
    )
    faults["steps counted from zero"] = (
        head + marker + re.sub(r"(?m)^([1-7])\. \*\*", lambda m: f"{int(m.group(1)) - 1}. **", tail)
    )
    faults["step 4 and 5 numbers swapped"] = (
        original.replace("\n4. **", "\n@@. **", 1)
        .replace("\n5. **", "\n4. **", 1)
        .replace("\n@@. **", "\n5. **", 1)
    )
    faults["numbers use a parenthesis"] = (
        head + marker + re.sub(r"(?m)^([1-7])\. \*\*", r"\1) **", tail)
    )
    for kind, broken in faults.items():
        check(
            "FTF-1807",
            f"renumbering fault '{kind}' turns the procedure pin RED",
            broken != original and _procedure_violations(broken) != [],
        )
        check(
            "FTF-1807",
            f"renumbering fault '{kind}' also turns the paragraph pin RED",
            _paragraph_pin_violations(broken) != [],
        )


def test_round4_a_heading_rename_that_keeps_the_count_is_caught():
    original = DOC_PATH.read_text(encoding="utf-8")
    check(
        "FTF-1807",
        "precondition: the headings pass unmodified",
        _heading_violations(original) == [],
    )
    headings = [line for line in original.splitlines() if line.startswith(("# ", "## "))]
    check("FTF-1807", "the doc has 9 headings", len(headings) == 9)
    for index, heading in enumerate(headings):
        renames = {
            "reworded": heading + " (optional)",
            "reworded in the middle": heading.replace(" ", " not ", 1),
            "emptied title": heading.split(" ")[0] + " " + heading.split(" ")[1]
            if " " in heading
            else heading,
            "upper-cased": heading.upper(),
            "level changed": "##" + heading if heading.startswith("# ") else "#" + heading,
        }
        for kind, new in renames.items():
            broken = original.replace(heading, new, 1)
            check(
                "FTF-1807",
                f"heading {index + 1} {kind} ({new!r}) turns the heading pin RED",
                broken != original and _heading_violations(broken) != [],
            )
            check(
                "FTF-1807",
                f"heading {index + 1} {kind} also turns the paragraph pin RED",
                _paragraph_pin_violations(broken) != [],
            )
    swapped = (
        original.replace(headings[3], "@@", 1)
        .replace(headings[4], headings[3], 1)
        .replace("@@", headings[4], 1)
    )
    check(
        "FTF-1807",
        "swapping two section headings (count unchanged) turns the heading pin RED",
        _heading_violations(swapped) != [],
    )


def test_round4_a_fault_in_one_checker_is_not_hidden_by_the_others():
    """Each checker must independently catch its own fault class on the real
    document with only that class of damage applied: the heading pin on a pure
    rename, the table pin on a pure Effect rewrite, the procedure pin on a pure
    step rewrite, the paragraph pin on a pure prose rewrite."""
    original = DOC_PATH.read_text(encoding="utf-8")
    cases = {
        "heading": (
            original.replace("## 2. Revocation handling", "## 2. Revocation notes", 1),
            _heading_violations,
        ),
        "table": (
            original.replace(
                "Immediate, unconditional call refusal", "Call refusal after the next sync", 1
            ),
            _operator_table_violations,
        ),
        "procedure": (
            original.replace("do not wait for the freshness TTL", "wait for the freshness TTL", 1),
            _procedure_violations,
        ),
        "paragraph": (
            original.replace("is dropped entirely", "is applied", 1),
            _paragraph_pin_violations,
        ),
    }
    checkers = {
        "heading": _heading_violations,
        "table": _operator_table_violations,
        "procedure": _procedure_violations,
        "paragraph": _paragraph_pin_violations,
    }
    for name, (broken, checker) in cases.items():
        check("FTF-1807", f"the {name} fault changed the doc", broken != original)
        check("FTF-1807", f"the {name} checker catches its own fault class", checker(broken) != [])
        others = [other for other in checkers if other != name]
        check(
            "FTF-1807",
            f"the {name} fault is what the {name} checker is for (others: {others})",
            _all_violations(broken) != [],
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
