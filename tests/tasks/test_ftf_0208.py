#!/usr/bin/env python3
"""FTF-0208 -- Last-known-good catalog.

Runnable without any database and without any external network egress for
its pure-state checks: `LastKnownGoodCatalog` is an in-memory, no-I/O object
(this task's own `database_changes` clause -- "No independent schema
change. Consume frozen contract.") that a caller feeds successive
`ModelDiscoveryResult` values into, typically produced by
`packages/litellm_client/v1_models_discovery.py::discover_models`
(FTF-0204, CERTIFIED). `test_real_gateway_sequential_discoveries_feed_
catalog_end_to_end` additionally proves the full, real, wired sequence: a
real local HTTPS gateway (with a real self-signed CA installed into this
sandbox's own trust store) whose responses genuinely change across three
real `discover_models` calls (healthy three-model list -> healthy
one-model list -> revoked credential), feeding this task's own catalog for
real -- same no-DB/no-external-egress local-server pattern
`tests/tasks/test_ftf_0204.py`/`tests/tasks/test_ftf_0207.py` (both already
CERTIFIED) established, reused here rather than introducing a separate
`tests/integration/` file and CI job for a task with no live route or
database of its own (this task's own `database_changes` clause; same
disclosed "no live HTTP route yet" scope FTF-0202/FTF-0205/FTF-0207 already
established).

This task's own acceptance clause ("Persist historical catalog and mark
stale/inaccessible separately from callable entries") is covered by
`test_first_successful_discovery_marks_all_entries_callable`,
`test_removed_entry_retained_in_snapshot_for_audit` and
`test_invalid_token_result_revokes_access_and_downgrades_callable_to_stale`.
This task's own negative_acceptance ("A last-known-good snapshot cannot
authorize queued/new inference after revocation or freshness expiration")
is covered by `test_authorization_denied_immediately_on_revocation_even_
if_still_fresh` and `test_authorization_denied_once_freshness_ttl_elapses`.

Run directly: python3 tests/tasks/test_ftf_0208.py
"""

from __future__ import annotations

import atexit
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    DEFAULT_FRESHNESS_TTL,
    AccessState,
    AuthorizationReason,
    CatalogEntry,
    CatalogEntryStatus,
    LastKnownGoodCatalog,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveredModel,
    DiscoveryState,
    ModelDiscoveryResult,
    ModelMetadataStatus,
    discover_models,
)
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []
_MODULE_PATH = ROOT / "packages" / "litellm_client" / "last_known_good_catalog.py"


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def _discovered(
    *ids: str, status: ModelMetadataStatus = ModelMetadataStatus.LIMITED
) -> ModelDiscoveryResult:
    models = tuple(DiscoveredModel(id=i, metadata_status=status) for i in ids)
    return ModelDiscoveryResult(
        state=DiscoveryState.DISCOVERED,
        models=models,
        model_count=len(models),
        content_hash="irrelevant-to-this-task",
        detail="gateway reachable and authorized",
        checked_at=_T0,
    )


def _invalid_token() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.INVALID_TOKEN,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway rejected the inference credential",
        checked_at=_T0,
    )


def _timeout() -> ModelDiscoveryResult:
    return ModelDiscoveryResult(
        state=DiscoveryState.TIMEOUT,
        models=(),
        model_count=None,
        content_hash=None,
        detail="gateway connection failed",
        checked_at=_T0,
    )


# -- Pure state-transition tests -----------------------------------------


def test_initial_snapshot_is_empty_and_unknown() -> None:
    catalog = LastKnownGoodCatalog()
    snapshot = catalog.snapshot()
    check("FTF-0208", "a fresh catalog has no entries", snapshot.entries == ())
    check(
        "FTF-0208",
        "a fresh catalog's access_state is unknown, never healthy by default",
        snapshot.access_state == AccessState.UNKNOWN,
    )
    check(
        "FTF-0208",
        "a fresh catalog has never had a successful check",
        snapshot.last_successful_check_at is None,
    )


def test_first_successful_discovery_marks_all_entries_callable() -> None:
    catalog = LastKnownGoodCatalog()
    snapshot = catalog.apply_discovery_result(_discovered("gpt-a", "gpt-b"), now=_T0)
    check(
        "FTF-0208",
        "every discovered id is persisted as its own catalog entry",
        {e.id for e in snapshot.entries} == {"gpt-a", "gpt-b"},
    )
    check(
        "FTF-0208",
        "a freshly discovered entry is marked callable, not stale or removed",
        all(e.status == CatalogEntryStatus.CALLABLE for e in snapshot.entries),
    )
    check(
        "FTF-0208",
        "a successful discovery moves access_state to healthy",
        snapshot.access_state == AccessState.HEALTHY,
    )
    check(
        "FTF-0208",
        "the successful check timestamp is recorded",
        snapshot.last_successful_check_at == _T0,
    )
    check("FTF-0208", "no alert on the very first discovery", snapshot.alert is None)


def test_first_seen_at_preserved_across_repeated_discoveries() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    later = _T0 + timedelta(minutes=1)
    snapshot = catalog.apply_discovery_result(_discovered("gpt-a"), now=later)
    entry = next(e for e in snapshot.entries if e.id == "gpt-a")
    check(
        "FTF-0208",
        "first_seen_at is never overwritten by a later, still-successful discovery",
        entry.first_seen_at == _T0,
    )
    check(
        "FTF-0208",
        "last_seen_callable_at is refreshed on each successful discovery",
        entry.last_seen_callable_at == later,
    )


def test_metadata_status_refreshed_on_each_successful_discovery() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(
        _discovered("gpt-a", status=ModelMetadataStatus.LIMITED), now=_T0
    )
    later = _T0 + timedelta(minutes=1)
    snapshot = catalog.apply_discovery_result(
        _discovered("gpt-a", status=ModelMetadataStatus.ENRICHED), now=later
    )
    entry = next(e for e in snapshot.entries if e.id == "gpt-a")
    check(
        "FTF-0208",
        "a later discovery's own metadata_status genuinely overwrites the stale value",
        entry.metadata_status == ModelMetadataStatus.ENRICHED,
    )


def test_removed_name_becomes_unselectable_immediately_after_successful_discovery() -> None:
    """Section 3.3: "On a successful authoritative list response, removed
    names immediately become unselectable even if deletion is
    suspicious." No TTL wait, no separate confirmation step required."""
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a", "gpt-b"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    catalog.apply_discovery_result(_discovered("gpt-a"), now=later)
    decision = catalog.is_call_authorized("gpt-b", now=later)
    check(
        "FTF-0208",
        "a name absent from the very next successful discovery is denied immediately",
        decision.authorized is False and decision.reason == AuthorizationReason.ENTRY_NOT_CALLABLE,
    )


def test_removed_entry_retained_in_snapshot_for_audit() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a", "gpt-b"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_discovered("gpt-a"), now=later)
    check(
        "FTF-0208",
        "a removed id is still present in the snapshot for audit/display, not deleted",
        {e.id for e in snapshot.entries} == {"gpt-a", "gpt-b"},
    )
    removed = next(e for e in snapshot.entries if e.id == "gpt-b")
    check(
        "FTF-0208",
        "the removed id's own status is marked removed, distinct from stale",
        removed.status == CatalogEntryStatus.REMOVED,
    )


def test_major_reduction_raises_alert_without_restoring_permissions() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("a", "b", "c", "d"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_discovered("a"), now=later)
    check(
        "FTF-0208",
        "a >50% drop in one cycle raises a real, content-free alert",
        snapshot.alert is not None
        and "sk-" not in snapshot.alert
        and "Bearer" not in snapshot.alert,
    )
    denied = catalog.is_call_authorized("b", now=later)
    check(
        "FTF-0208",
        "the alert never restores the dropped ids' own permission -- still unselectable",
        denied.authorized is False,
    )


def test_minor_reduction_does_not_raise_alert() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("a", "b", "c", "d"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_discovered("a", "b", "c"), now=later)
    check(
        "FTF-0208",
        "dropping one of four (25%) is not an 'unexpected' reduction",
        snapshot.alert is None,
    )


def test_invalid_token_result_revokes_access_and_downgrades_callable_to_stale() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_invalid_token(), now=later)
    check(
        "FTF-0208",
        "a 401/403 discovery result immediately revokes access_state",
        snapshot.access_state == AccessState.REVOKED,
    )
    entry = next(e for e in snapshot.entries if e.id == "gpt-a")
    check(
        "FTF-0208",
        "a previously callable entry is downgraded to stale, not removed, on revocation",
        entry.status == CatalogEntryStatus.STALE,
    )
    check(
        "FTF-0208",
        "revocation does not fabricate a new successful-check timestamp",
        snapshot.last_successful_check_at == _T0,
    )


def test_authorization_denied_immediately_on_revocation_even_if_still_fresh() -> None:
    """This task's own negative_acceptance, the "after revocation" half:
    denial is immediate, not merely eventual once the freshness TTL
    happens to elapse."""
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    just_after = _T0 + timedelta(seconds=1)
    catalog.apply_discovery_result(_invalid_token(), now=just_after)
    decision = catalog.is_call_authorized("gpt-a", now=just_after)
    check(
        "FTF-0208",
        "a revoked connection denies a still-fresh, previously callable id immediately",
        decision.authorized is False and decision.reason == AuthorizationReason.ACCESS_REVOKED,
    )


def test_revocation_is_idempotent_and_does_not_resurrect_removed_entries() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a", "gpt-b"), now=_T0)
    t1 = _T0 + timedelta(seconds=1)
    catalog.apply_discovery_result(_discovered("gpt-a"), now=t1)  # gpt-b removed
    t2 = t1 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_invalid_token(), now=t2)
    t3 = t2 + timedelta(seconds=1)
    snapshot2 = catalog.apply_discovery_result(_invalid_token(), now=t3)
    removed_entry = next(e for e in snapshot2.entries if e.id == "gpt-b")
    check(
        "FTF-0208",
        "a repeated revocation never resurrects an already-removed entry back to stale",
        removed_entry.status == CatalogEntryStatus.REMOVED,
    )
    check(
        "FTF-0208",
        "repeated revocation stays revoked, deterministically",
        snapshot.access_state == AccessState.REVOKED
        and snapshot2.access_state == AccessState.REVOKED,
    )


def test_unknown_failure_does_not_revoke_or_change_entries() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_timeout(), now=later)
    check(
        "FTF-0208",
        "an ordinary connectivity failure is not treated as a confirmed revocation",
        snapshot.access_state == AccessState.DEGRADED,
    )
    entry = next(e for e in snapshot.entries if e.id == "gpt-a")
    check(
        "FTF-0208",
        "an ordinary connectivity failure never touches an existing entry's own status",
        entry.status == CatalogEntryStatus.CALLABLE,
    )


def test_unknown_failure_does_not_refresh_freshness_clock() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    later = _T0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(_timeout(), now=later)
    check(
        "FTF-0208",
        "a failed probe never counts as a fresh successful check",
        snapshot.last_successful_check_at == _T0,
    )


def test_authorization_denied_before_any_successful_discovery() -> None:
    catalog = LastKnownGoodCatalog()
    decision = catalog.is_call_authorized("gpt-a", now=_T0)
    check(
        "FTF-0208",
        "a catalog with no successful discovery yet authorizes nothing",
        decision.authorized is False
        and decision.reason == AuthorizationReason.NO_SUCCESSFUL_DISCOVERY_YET,
    )


def test_authorization_denied_once_freshness_ttl_elapses() -> None:
    """This task's own negative_acceptance, the "freshness expiration"
    half. No revocation ever occurs here -- the last known good list is
    simply too old to keep trusting."""
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    stale_at = _T0 + DEFAULT_FRESHNESS_TTL
    decision = catalog.is_call_authorized("gpt-a", now=stale_at)
    check(
        "FTF-0208",
        "authorization is denied exactly once the default TTL has elapsed",
        decision.authorized is False and decision.reason == AuthorizationReason.FRESHNESS_EXPIRED,
    )


def test_authorization_allowed_within_freshness_ttl_for_callable_entry() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    just_before_expiry = _T0 + DEFAULT_FRESHNESS_TTL - timedelta(seconds=1)
    decision = catalog.is_call_authorized("gpt-a", now=just_before_expiry)
    check(
        "FTF-0208",
        "a callable entry within TTL, with healthy access, is genuinely authorized",
        decision.authorized is True and decision.reason == AuthorizationReason.CALLABLE,
    )


def test_authorization_denied_for_unknown_model_id() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    decision = catalog.is_call_authorized("never-discovered", now=_T0)
    check(
        "FTF-0208",
        "an id this catalog has never seen is never authorized",
        decision.authorized is False and decision.reason == AuthorizationReason.NOT_IN_CATALOG,
    )


def test_custom_freshness_ttl_is_respected() -> None:
    catalog = LastKnownGoodCatalog()
    catalog.apply_discovery_result(_discovered("gpt-a"), now=_T0)
    short_ttl = timedelta(seconds=30)
    still_ok = catalog.is_call_authorized(
        "gpt-a", now=_T0 + timedelta(seconds=10), freshness_ttl=short_ttl
    )
    expired = catalog.is_call_authorized(
        "gpt-a", now=_T0 + timedelta(seconds=31), freshness_ttl=short_ttl
    )
    check(
        "FTF-0208",
        "a caller-supplied shorter TTL is honored while still fresh",
        still_ok.authorized,
    )
    check(
        "FTF-0208",
        "the same caller-supplied TTL is honored once elapsed",
        expired.authorized is False and expired.reason == AuthorizationReason.FRESHNESS_EXPIRED,
    )


def test_catalog_entry_dataclass_is_frozen() -> None:
    entry = CatalogEntry(
        id="gpt-a",
        metadata_status=ModelMetadataStatus.LIMITED,
        status=CatalogEntryStatus.CALLABLE,
        first_seen_at=_T0,
        last_seen_callable_at=_T0,
    )
    try:
        entry.id = "mutated"  # type: ignore[misc]
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    check("FTF-0208", "CatalogEntry is immutable", raised)


def test_apply_discovery_result_never_raises_for_any_state() -> None:
    for state in DiscoveryState:
        catalog = LastKnownGoodCatalog()
        result = ModelDiscoveryResult(
            state=state,
            models=(),
            model_count=None if state != DiscoveryState.DISCOVERED else 0,
            content_hash=None,
            detail="synthetic",
            checked_at=_T0,
        )
        try:
            catalog.apply_discovery_result(result, now=_T0)
            raised = False
        except Exception:  # noqa: BLE001
            raised = True
        check("FTF-0208", f"applying a {state.value} result never raises", not raised)


# -- Real gateway integration test ---------------------------------------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-inference-key-0208"
_SECRET_TOKEN = "test-vault-token"


class _SecretBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _json(self, status: int, payload: dict[str, object] | None) -> None:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        token = self.headers.get("X-Vault-Token")
        if token != _SECRET_TOKEN:
            self._json(403, {"errors": ["permission denied"]})
            return
        if self.path == f"/v1/{_SECRET_MOUNT}/data/{_SECRET_PATH}":
            self._json(
                200,
                {"data": {"data": {_SECRET_FIELD: _SECRET_VALUE}, "metadata": {"version": 1}}},
            )
        else:
            self._json(404, {"errors": []})


_SECRET_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBackendHandler)
_SECRET_PORT = _SECRET_SERVER.server_address[1]
threading.Thread(target=_SECRET_SERVER.serve_forever, daemon=True).start()
atexit.register(_SECRET_SERVER.shutdown)

_SECRET_CONFIG = OpenBaoConfig(address=f"http://127.0.0.1:{_SECRET_PORT}", token=_SECRET_TOKEN)


def _secret_ref() -> str:
    return f"openbao://{_SECRET_MOUNT}/{_SECRET_PATH}/{_SECRET_FIELD}"


_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-0208-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-0208-test-ca.crt")
_UPDATE_CA_AVAILABLE = (
    shutil.which("update-ca-certificates") is not None
    and shutil.which("openssl") is not None
    and shutil.which("sudo") is not None
    and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
)


def _run_openssl(args: list[str]) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def _generate_trusted_server_cert() -> tuple[Path, Path]:
    ca_key = _TLS_TMP / "ca.key"
    ca_crt = _TLS_TMP / "ca.crt"
    leaf_key = _TLS_TMP / "leaf.key"
    leaf_csr = _TLS_TMP / "leaf.csr"
    leaf_crt = _TLS_TMP / "leaf.crt"
    leaf_ext = _TLS_TMP / "leaf.ext"

    _run_openssl(["genrsa", "-out", str(ca_key), "2048"])
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            "2",
            "-out",
            str(ca_crt),
            "-subj",
            "/CN=FTF-0208 Test CA",
        ]
    )
    _run_openssl(["genrsa", "-out", str(leaf_key), "2048"])
    _run_openssl(
        ["req", "-new", "-key", str(leaf_key), "-out", str(leaf_csr), "-subj", "/CN=127.0.0.1"]
    )
    leaf_ext.write_text("subjectAltName=IP:127.0.0.1\n")
    _run_openssl(
        [
            "x509",
            "-req",
            "-in",
            str(leaf_csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(leaf_crt),
            "-days",
            "2",
            "-sha256",
            "-extfile",
            str(leaf_ext),
        ]
    )

    subprocess.run(
        ["sudo", "cp", str(ca_crt), str(_CA_TRUST_TARGET)], check=True, capture_output=True
    )
    subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    atexit.register(_remove_ca_trust)
    return leaf_crt, leaf_key


def _remove_ca_trust() -> None:
    subprocess.run(["sudo", "rm", "-f", str(_CA_TRUST_TARGET)], check=False, capture_output=True)
    subprocess.run(["sudo", "update-ca-certificates", "--fresh"], check=False, capture_output=True)


_TRUSTED_LEAF: Path | None = None
_TRUSTED_KEY: Path | None = None
if _UPDATE_CA_AVAILABLE:
    _TRUSTED_LEAF, _TRUSTED_KEY = _generate_trusted_server_cert()
else:
    print(
        "FTF-0208: this sandbox has no openssl/update-ca-certificates -- the "
        "live real-gateway end-to-end check is skipped and disclosed here; "
        "every pure catalog-state check still runs and still counts. "
        "GitHub Actions' ubuntu-latest runner has both tools."
    )


def _policy_for(port: int) -> ConnectionPolicy:
    import ipaddress

    return ConnectionPolicy(
        name="test-litellm-gateway",
        allowed_schemes=frozenset({"https"}),
        allowed_ports=frozenset({port}),
        explicit_private_allowlist=(ipaddress.ip_network("127.0.0.0/8"),),
    )


def _start_https_server(handler_cls: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    assert _TRUSTED_LEAF is not None and _TRUSTED_KEY is not None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(_TRUSTED_LEAF), keyfile=str(_TRUSTED_KEY))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    atexit.register(server.shutdown)
    return server


class _SequenceState:
    """Mutable step counter the handler reads per request -- lets one real
    server genuinely change behavior across three sequential real calls
    (healthy three-model list -> healthy one-model list -> revoked)."""

    step = 0


def _make_sequential_gateway_handler(state: _SequenceState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/v1/models":
                self._respond(404, b"")
                return
            auth = self.headers.get("Authorization", "")
            if state.step == 2:
                self._respond(401, b'{"error":"invalid token"}')
                return
            if auth != f"Bearer {_SECRET_VALUE}":
                self._respond(401, b'{"error":"invalid token"}')
                return
            if state.step == 0:
                ids = ["gpt-a", "gpt-b", "gpt-c"]
            else:
                ids = ["gpt-a"]
            self._respond(200, json.dumps({"data": [{"id": i} for i in ids]}).encode("utf-8"))

    return _Handler


def test_real_gateway_sequential_discoveries_feed_catalog_end_to_end() -> None:
    """Proves the full real pipeline, not just the pure-state transitions
    in isolation: three genuine `discover_models` calls against one real
    local HTTPS gateway (whose real responses change per call) feed one
    real `LastKnownGoodCatalog`, and this task's own acceptance/
    negative_acceptance both hold against those real results."""
    if not _UPDATE_CA_AVAILABLE:
        return
    state = _SequenceState()
    server = _start_https_server(_make_sequential_gateway_handler(state))
    port = server.server_address[1]
    base_url = f"https://127.0.0.1:{port}"
    policy = _policy_for(port)
    catalog = LastKnownGoodCatalog()

    state.step = 0
    first = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    check(
        "FTF-0208",
        "the real gateway's first call is genuinely authorized with three models",
        first.state == DiscoveryState.DISCOVERED and first.model_count == 3,
    )
    t0 = datetime.now(UTC)
    catalog.apply_discovery_result(first, now=t0)
    check(
        "FTF-0208",
        "all three real ids are callable right after the first real discovery",
        all(
            catalog.is_call_authorized(mid, now=t0).authorized
            for mid in ("gpt-a", "gpt-b", "gpt-c")
        ),
    )

    state.step = 1
    second = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    check(
        "FTF-0208",
        "the real gateway's second call genuinely returns only one model",
        second.state == DiscoveryState.DISCOVERED and second.model_count == 1,
    )
    t1 = t0 + timedelta(seconds=1)
    snapshot = catalog.apply_discovery_result(second, now=t1)
    check(
        "FTF-0208",
        "a real >50% drop against the real gateway raises a real alert",
        snapshot.alert is not None,
    )
    check(
        "FTF-0208",
        "the two really-dropped ids are genuinely denied right away, not just flagged",
        not catalog.is_call_authorized("gpt-b", now=t1).authorized
        and not catalog.is_call_authorized("gpt-c", now=t1).authorized,
    )
    check(
        "FTF-0208",
        "the still-present real id stays genuinely authorized",
        catalog.is_call_authorized("gpt-a", now=t1).authorized,
    )

    state.step = 2
    third = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    check(
        "FTF-0208",
        "the real gateway's third call genuinely rejects the credential",
        third.state == DiscoveryState.INVALID_TOKEN,
    )
    t2 = t1 + timedelta(seconds=1)
    catalog.apply_discovery_result(third, now=t2)
    check(
        "FTF-0208",
        "a real revocation genuinely denies the previously-callable real id immediately",
        not catalog.is_call_authorized("gpt-a", now=t2).authorized,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(f"FTF-0208 last-known-good catalog: {len(FAILURES)} failed")
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-0208 last-known-good catalog: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
