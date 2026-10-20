#!/usr/bin/env python3
"""FTF-H004 -- Authorized LiteLLM catalog and revocation semantics.

`packages/model_catalog/access_snapshot.py::build_access_snapshot` is a
pure, synchronous, no-I/O function -- most checks below build a
`CatalogSnapshot`/`AliasReconciliationResult` directly as dataclasses, no
HTTP server or database needed. `test_real_gateway_snapshot_never_exposes_
metadata_only_model_and_survives_revocation` additionally proves the full,
real, wired pipeline: a real local HTTPS gateway (with a real self-signed
CA installed into this sandbox's own trust store) and a real local
OpenBao-shaped secret-backend fixture feed genuine `discover_models`/
`enrich_visible_models`/`reconcile_aliases` calls across two real,
sequential round trips (healthy credential -> revoked credential) into one
real `LastKnownGoodCatalog`, whose real snapshots this task's own
`build_access_snapshot` then combines -- same no-DB/no-external-egress
local-server pattern `tests/tasks/test_ftf_0207.py`/
`tests/tasks/test_ftf_0208.py` (both already CERTIFIED) established.

`packages/litellm_client/access_guard.py::require_model_access`'s own real
cross-tenant/revoked-connection/revoked-access denial evidence needs a real
Postgres (it composes FTF-H003's own `authorize_resource_ownership`) and is
proven separately, against a live database, in
`tests/integration/test_ftf_h004_access_guard.py`.

This task's own goal ("Separate historic catalog from current callable
set; left-join only matching safe metadata, track access freshness and
fail closed on revoked/missing grants") is covered by
`test_callable_entry_receives_matching_reconciled_fields`,
`test_stale_entry_never_receives_fields_even_if_reconciliation_reports_them`,
`test_removed_entry_never_receives_fields`,
`test_reconciliation_id_absent_from_catalog_never_creates_a_new_entry`,
`test_missing_reconciliation_leaves_every_entry_with_empty_fields` and
`test_access_state_and_alert_pass_through_unchanged`. The real-gateway
negative evidence ("Real restricted gateway key cannot enumerate/probe
metadata-only models"; "revoke a once-valid model/key and verify new
queued actions fail while history remains visible") is
`test_real_gateway_snapshot_never_exposes_metadata_only_model_and_survives_revocation`.

Run directly: python3 tests/tasks/test_ftf_h004.py
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
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.litellm_client.alias_reconciliation import (  # noqa: E402
    AliasReconciliationResult,
    ReconciledModel,
    reconcile_aliases,
)
from packages.litellm_client.last_known_good_catalog import (  # noqa: E402
    AccessState,
    CatalogEntry,
    CatalogEntryStatus,
    CatalogSnapshot,
    LastKnownGoodCatalog,
)
from packages.litellm_client.secret_manager_integration import OpenBaoConfig  # noqa: E402
from packages.litellm_client.v1_model_info_enrichment import (  # noqa: E402
    EnrichmentState,
    enrich_visible_models,
)
from packages.litellm_client.v1_models_discovery import (  # noqa: E402
    DiscoveryState,
    ModelMetadataStatus,
    discover_models,
)
from packages.model_catalog.access_snapshot import build_access_snapshot  # noqa: E402
from packages.security.url_policy import ConnectionPolicy  # noqa: E402

FAILURES: list[str] = []


def check(criterion_id: str, description: str, condition: bool) -> None:
    if not condition:
        FAILURES.append(f"FAIL [{criterion_id}] {description}")


_NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _catalog_entry(
    model_id: str,
    status: CatalogEntryStatus,
    *,
    metadata_status: ModelMetadataStatus = ModelMetadataStatus.LIMITED,
) -> CatalogEntry:
    return CatalogEntry(
        id=model_id,
        metadata_status=metadata_status,
        status=status,
        first_seen_at=_NOW,
        last_seen_callable_at=_NOW if status != CatalogEntryStatus.REMOVED else None,
    )


def _catalog_snapshot(
    *entries: CatalogEntry,
    access_state: AccessState = AccessState.HEALTHY,
    alert: str | None = None,
) -> CatalogSnapshot:
    return CatalogSnapshot(
        entries=entries,
        access_state=access_state,
        last_successful_check_at=_NOW,
        last_checked_at=_NOW,
        alert=alert,
    )


def _reconciled(model_id: str, fields: dict[str, object] | None = None) -> ReconciledModel:
    resolved = MappingProxyType(dict(fields) if fields else {})
    status = ModelMetadataStatus.ENRICHED if resolved else ModelMetadataStatus.LIMITED
    return ReconciledModel(id=model_id, metadata_status=status, fields=resolved)


def _reconciliation(*models: ReconciledModel) -> AliasReconciliationResult:
    return AliasReconciliationResult(models=models, model_count=len(models), reconciled_at=_NOW)


# -- Pure, no-I/O tests ------------------------------------------------------


def test_callable_entry_receives_matching_reconciled_fields() -> None:
    snapshot = _catalog_snapshot(_catalog_entry("gpt-x", CatalogEntryStatus.CALLABLE))
    reconciliation = _reconciliation(_reconciled("gpt-x", {"mode": "chat", "max_tokens": 4096}))
    view = build_access_snapshot(snapshot, reconciliation)
    check("FTF-H004", "exactly one entry in the combined view", len(view.entries) == 1)
    entry = view.entries[0]
    check(
        "FTF-H004",
        "a callable entry receives its real matching reconciled fields",
        entry.id == "gpt-x" and dict(entry.fields) == {"mode": "chat", "max_tokens": 4096},
    )


def test_stale_entry_never_receives_fields_even_if_reconciliation_reports_them() -> None:
    """This task's own goal: "left-join only matching safe metadata" --
    a stale (revoked/expired) catalog entry must never surface metadata,
    even if a caller passes a fresh reconciliation that still reports real
    fields for that exact id (e.g. a slow/duplicate cycle)."""
    snapshot = _catalog_snapshot(
        _catalog_entry("gpt-old", CatalogEntryStatus.STALE), access_state=AccessState.REVOKED
    )
    reconciliation = _reconciliation(_reconciled("gpt-old", {"mode": "chat"}))
    view = build_access_snapshot(snapshot, reconciliation)
    entry = view.entries[0]
    check(
        "FTF-H004",
        "a stale entry's fields stay empty regardless of what a fresh reconciliation reports",
        entry.status == CatalogEntryStatus.STALE and dict(entry.fields) == {},
    )
    check(
        "FTF-H004",
        "the stale entry itself remains visible in the combined view (audit/history)",
        entry.id == "gpt-old",
    )


def test_removed_entry_never_receives_fields() -> None:
    snapshot = _catalog_snapshot(_catalog_entry("gpt-gone", CatalogEntryStatus.REMOVED))
    reconciliation = _reconciliation(_reconciled("gpt-gone", {"mode": "chat"}))
    view = build_access_snapshot(snapshot, reconciliation)
    entry = view.entries[0]
    check(
        "FTF-H004",
        "a removed entry's fields stay empty even if reconciliation still names it",
        entry.status == CatalogEntryStatus.REMOVED and dict(entry.fields) == {},
    )


def test_reconciliation_id_absent_from_catalog_never_creates_a_new_entry() -> None:
    """Section 3.2's "no authorization union", reapplied as this module's
    own independent boundary: a reconciled model the catalog has never
    seen at all must never become a new displayed entry."""
    snapshot = _catalog_snapshot(_catalog_entry("gpt-x", CatalogEntryStatus.CALLABLE))
    reconciliation = _reconciliation(
        _reconciled("gpt-x", {"mode": "chat"}),
        _reconciled("management-only-model", {"mode": "chat"}),
    )
    view = build_access_snapshot(snapshot, reconciliation)
    ids = [entry.id for entry in view.entries]
    check(
        "FTF-H004",
        "the combined view has exactly the catalog's own entries -- never widened by "
        "a reconciliation entry the catalog has not itself seen",
        ids == ["gpt-x"] and "management-only-model" not in ids,
    )


def test_missing_reconciliation_leaves_every_entry_with_empty_fields() -> None:
    snapshot = _catalog_snapshot(_catalog_entry("gpt-x", CatalogEntryStatus.CALLABLE))
    view = build_access_snapshot(snapshot, None)
    check(
        "FTF-H004",
        "with no fresh reconciliation supplied, fields stay empty rather than stale/invented",
        dict(view.entries[0].fields) == {},
    )


def test_access_state_and_alert_pass_through_unchanged() -> None:
    snapshot = _catalog_snapshot(
        _catalog_entry("gpt-x", CatalogEntryStatus.CALLABLE),
        access_state=AccessState.DEGRADED,
        alert="authorized model count dropped",
    )
    view = build_access_snapshot(snapshot, None)
    check(
        "FTF-H004",
        "access_state/alert/timestamps are the catalog's own, never recomputed here",
        view.access_state == AccessState.DEGRADED
        and view.alert == "authorized model count dropped"
        and view.last_successful_check_at == _NOW
        and view.last_checked_at == _NOW,
    )


# -- Real local secret-backend fixture (OpenBao KV v2 read subset), same
# convention tests/tasks/test_ftf_0204.py/0207.py/0208.py already established
# ---------------------------------------------------------------------------

_SECRET_MOUNT = "secret"
_SECRET_PATH = "litellm/org1"
_SECRET_FIELD = "api_key"
_SECRET_VALUE = "sk-real-inference-key-h004"
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


# -- Real local TLS fixture: same convention as tests/tasks/test_ftf_0204.py/
# test_ftf_0207.py/test_ftf_0208.py -----------------------------------------

_TLS_TMP = Path(tempfile.mkdtemp(prefix="ftf-h004-tls-"))
_CA_TRUST_TARGET = Path("/usr/local/share/ca-certificates/ftf-h004-test-ca.crt")
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
            "/CN=FTF-H004 Test CA",
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
        "FTF-H004: this sandbox has no openssl/update-ca-certificates -- the "
        "live real-gateway end-to-end check is skipped and disclosed here; "
        "every pure snapshot-composition check still runs and still counts. "
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
    server genuinely change behavior across two sequential real cycles
    (healthy credential -> revoked credential), same convention
    tests/tasks/test_ftf_0208.py's own `_SequenceState` already established."""

    step = 0


def _make_sequential_gateway_handler(state: _SequenceState) -> type[BaseHTTPRequestHandler]:
    """A real, minimal LiteLLM-shaped gateway. `/v1/models` lists exactly
    two authorized aliases (`gpt-x`, `gpt-y`) while `state.step == 0`, and
    rejects the credential once `state.step == 1` (a real revocation).
    `/model/info` additionally, at every step, advertises a third alias
    (`internal-mgmt-model`) the gateway never lists as authorized -- this
    task's own "Real restricted gateway key cannot enumerate/probe
    metadata-only models" negative evidence, proven through a real round
    trip rather than only at the unit level."""

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
            auth = self.headers.get("Authorization", "")
            if state.step == 1 or auth != f"Bearer {_SECRET_VALUE}":
                self._respond(401, b'{"error":"invalid token"}')
                return
            if self.path == "/v1/models":
                self._respond(
                    200,
                    json.dumps({"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]}).encode("utf-8"),
                )
                return
            if self.path == "/model/info":
                self._respond(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {"model_name": "gpt-x", "model_info": {"mode": "chat"}},
                                {
                                    "model_name": "internal-mgmt-model",
                                    "model_info": {"mode": "chat"},
                                },
                            ]
                        }
                    ).encode("utf-8"),
                )
                return
            self._respond(404, b"")

    return _Handler


def test_real_gateway_snapshot_never_exposes_metadata_only_model_and_survives_revocation() -> None:
    if not _UPDATE_CA_AVAILABLE:
        return
    state = _SequenceState()
    server = _start_https_server(_make_sequential_gateway_handler(state))
    port = server.server_address[1]
    base_url = f"https://127.0.0.1:{port}"
    policy = _policy_for(port)
    catalog = LastKnownGoodCatalog()

    # -- Cycle 1: a real, healthy credential ---------------------------------
    state.step = 0
    discovery = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    check(
        "FTF-H004",
        "the real gateway's first call is genuinely authorized with exactly gpt-x/gpt-y",
        discovery.state == DiscoveryState.DISCOVERED
        and {m.id for m in discovery.models} == {"gpt-x", "gpt-y"},
    )
    enrichment = enrich_visible_models(
        base_url=base_url,
        secret_ref=_secret_ref(),
        secret_config=_SECRET_CONFIG,
        policy=policy,
        visible_model_ids=tuple(m.id for m in discovery.models),
    )
    check(
        "FTF-H004",
        "the real gateway's /model/info call genuinely completes",
        enrichment.state == EnrichmentState.COMPLETED,
    )
    reconciliation = reconcile_aliases(discovery=discovery, enrichment=enrichment)

    t0 = datetime.now(UTC)
    catalog_snapshot = catalog.apply_discovery_result(discovery, now=t0)
    view = build_access_snapshot(catalog_snapshot, reconciliation)
    by_id = {entry.id: entry for entry in view.entries}
    check(
        "FTF-H004",
        "the combined snapshot has exactly the two really-authorized aliases -- the real "
        "gateway's metadata-only alias never appears, callable or not",
        set(by_id) == {"gpt-x", "gpt-y"},
    )
    check(
        "FTF-H004",
        "the callable, really-enriched alias carries its real safe fields",
        by_id["gpt-x"].status == CatalogEntryStatus.CALLABLE
        and dict(by_id["gpt-x"].fields) == {"mode": "chat"},
    )
    check(
        "FTF-H004",
        "the callable alias with no real /model/info row stays callable with empty fields",
        by_id["gpt-y"].status == CatalogEntryStatus.CALLABLE and dict(by_id["gpt-y"].fields) == {},
    )

    # -- Cycle 2: the real gateway genuinely revokes the credential ---------
    state.step = 1
    revoked_discovery = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    check(
        "FTF-H004",
        "the real gateway's second call genuinely rejects the credential",
        revoked_discovery.state == DiscoveryState.INVALID_TOKEN,
    )
    t1 = t0 + timedelta(seconds=1)
    revoked_snapshot = catalog.apply_discovery_result(revoked_discovery, now=t1)
    revoked_view = build_access_snapshot(revoked_snapshot, None)
    revoked_by_id = {entry.id: entry for entry in revoked_view.entries}
    check(
        "FTF-H004",
        "after a real revocation, the previously-callable real alias remains visible for "
        "audit -- history is not deleted",
        "gpt-x" in revoked_by_id,
    )
    check(
        "FTF-H004",
        "after a real revocation, the previously-callable real alias is downgraded to "
        "stale, with no fields surfaced",
        revoked_by_id["gpt-x"].status == CatalogEntryStatus.STALE
        and dict(revoked_by_id["gpt-x"].fields) == {},
    )
    check(
        "FTF-H004",
        "the real revocation sets access_state=revoked on the combined view too",
        revoked_view.access_state == AccessState.REVOKED,
    )

    # -- Negative_acceptance: a retried, still-failing real call cannot -----
    # -- silently restore access -------------------------------------------
    still_revoked = discover_models(
        base_url=base_url, secret_ref=_secret_ref(), secret_config=_SECRET_CONFIG, policy=policy
    )
    t2 = t1 + timedelta(seconds=1)
    still_snapshot = catalog.apply_discovery_result(still_revoked, now=t2)
    check(
        "FTF-H004",
        "a real retry of a still-revoked credential cannot silently restore access -- "
        "access_state stays revoked, not reset to healthy or degraded",
        still_snapshot.access_state == AccessState.REVOKED
        and not catalog.is_call_authorized("gpt-x", now=t2).authorized,
    )


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()

    if FAILURES:
        print(
            f"FTF-H004 authorized LiteLLM catalog and revocation semantics: {len(FAILURES)} failed"
        )
        for failure in FAILURES:
            print(f"  {failure}")
        sys.exit(1)

    print("FTF-H004 authorized LiteLLM catalog and revocation semantics: all checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
